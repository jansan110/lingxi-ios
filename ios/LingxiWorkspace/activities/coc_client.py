"""Shared China Mobile activity authentication and request transport."""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app_login import AppCredentialError, AppLoginError, AppLoginSession
from http_transport import requests
from recharge.cmcc import APP_UA
from star_day_auth import encrypt_uid
from .coc_environment import collect, collect_mobile

ORIGIN = 'https://dev.coc.10086.cn'
API = ORIGIN + '/coc/'
# Preserve the stored scope for the COC login already shared with seven-day.
COC_SCOPE = 'seven-day'
APP_SESSION_REJECTION_CODES = {'400007', '400009', '410000', '420000'}

PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0a66eNBd/ioaGnEM2AV+ctQX8VYt6dszzI8zOv26Kc1A7ex/cu/txmbB6WrU5ARoTeS0gk3KS21e/xP1BR1FUqL/+YJ4v8gpFY21/2gWuEPVBoHEd34FMpDRuiQkpmrvuuRF+QJQIg5ASEBpwz4n15ltl7Fd4E+NLp/JRJxzNR55ZeVLLFaucWYe8FIvX5BKx8i+sJWZYDoxTbjrBTy2CZDZObT+PxhOBXsCkolsG8Ux+tIAoU+UAVSQ0qT62z/sHPVPngHAqmLP/sF3IM6Mrub7BqhwJe3rvddqrw+Q2Jg8upZ/oUlZvXtQZCxJXtg0eJVdKTmnE6hD+/M99ukEoQIDAQAB'

class CocError(Exception):
    def __init__(self, message, code='COC_ERROR', status=502, *, uncertain=False, internal_message=''):
        super().__init__(message)
        self.code, self.status, self.uncertain = code, status, uncertain
        self.internal_message = internal_message
        self.trace_id = ''


def _trace_id(response):
    value = getattr(response, 'headers', {}).get('trace-id', '')
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:/=-]{1,100}', value) else ''


def _aes_encrypt(value, key):
    pad = padding.PKCS7(128).padder()
    plain = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(cipher.update(pad.update(plain) + pad.finalize()) + cipher.finalize()).decode()


def _encrypted(value, kind):
    key = secrets.token_hex(8).encode()
    public = serialization.load_der_public_key(base64.b64decode(PUBLIC_KEY))
    content = _aes_encrypt(value, key)
    if kind == 'meteor':
        header = {'coc-meteor': base64.b64encode(public.encrypt(base64.b64encode(key), rsa_padding.PKCS1v15())).decode(), 'coc-meteor-v': '2'}
        content = base64.b64encode(content.encode()).decode()
    else:
        header = {'Coc-En-Version': '1486234121417629696', 'coc-aurora-v': '2',
                  'coc-aurora': base64.b64encode(public.encrypt(key, rsa_padding.PKCS1v15())).decode()}
    return {'cocEnContent': content}, header, key


def _decode(response, key=None):
    if not isinstance(response, dict):
        raise CocError('官方响应格式异常', 'UPSTREAM_FORMAT')
    if key is not None and response.get('returnData'):
        cipher = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        unpad = padding.PKCS7(128).unpadder()
        raw = cipher.update(base64.b64decode(response['returnData'])) + cipher.finalize()
        response = json.loads((unpad.update(raw) + unpad.finalize()).decode())
    if str(response.get('code')) != '0':
        code = str(response.get('code', 'UNKNOWN'))[:60]
        message = str(response.get('message') or '官方暂未完成此操作')[:300]
        detail = response.get('messageInternal')
        raise CocError(message + '（' + code + '）', code, 401 if code == '40' else 502,
                       internal_message=detail if isinstance(detail, str) else '')
    return response.get('data')


class CocClient:
    def __init__(self, credentials_provider, *, entry, session_factory=None, credential_cache=None,
                 app_session_recover=None, sdk_encrypt=None, credential_scope=COC_SCOPE,
                 request_headers=None, app_token_source=None, credential_aliases=()):
        self.entry = entry
        query = parse_qs(urlsplit(entry).query)
        self.page_id = query.get('pageId', [''])[0]
        self.channel = query.get('channelId', [''])[0]
        self.sdk_encrypt = sdk_encrypt
        self.credentials_provider = credentials_provider
        self.session_factory = session_factory or (lambda: requests.Session(impersonate='chrome'))
        self.credential_cache = credential_cache
        self.credential_scope = credential_scope
        # These legacy names authenticate the same COC realm. All current
        # callers use COC_SCOPE so concurrent activity entry shares one lock
        # and cannot create competing logins for the same account.
        self.credential_aliases = (tuple(dict.fromkeys((*credential_aliases,'net-age','net-age-ydrz')))
                                   if credential_scope==COC_SCOPE else tuple(credential_aliases))
        self.request_headers = dict(request_headers or {})
        self.app_token_source = app_token_source
        self.app_session_recover = app_session_recover
        self.identity_recovered = False
        self.client = None
        self.snapshot = None
        self.user = {}
        self.last_wheel_diagnostic = {}
        self.wheel_runtime = None
        self.wheel_wire_body = None
        self.last_wheel_exchange = {}

    def current(self):
        latest = self.credentials_provider()
        if (latest is None or latest.get('phone') != self.snapshot.get('phone')
                or self.credential_cache is None and latest.get('cookie') != self.snapshot['cookie']):
            raise CocError('共享账号登录已更新，请重新获取本次验证', 'APP_CREDENTIALS_CHANGED', 409)

    def connect(self):
        snapshot = self.credentials_provider()
        if snapshot is None or not re.fullmatch(r'1[3-9]\d{9}', str(snapshot.get('phone', ''))):
            raise CocError('共享账号需要重新登录，请在账号管理恢复登录', 'APP_LOGIN_REQUIRED', 401)
        self.snapshot = snapshot
        self.client = self.session_factory()
        self.client.headers.update({
            'User-Agent': APP_UA, 'Origin': ORIGIN, 'Referer': self.entry,
            'Accept-Language': 'zh-CN,zh;q=0.9', **self.request_headers,
        })
        if self.credential_cache is None:
            self._authenticate()
        else:
            data = self.credential_cache.connect(self.credential_scope,snapshot,self.client,
                self._authenticate, expiry_cookies=('User-Token',),
                **({'aliases':self.credential_aliases} if self.credential_aliases else {}))
            self.user = data['user']
        return self

    def _ensure_page_cookies(self):
        if not self.client.cookies.get('gdp_user_id'):
            alphabet='0123456789abcdefghijklmnopqrstuvwxyz'
            visitor=','.join((
                ''.join(secrets.choice(alphabet) for _ in range(6))+'-'+''.join(secrets.choice(alphabet) for _ in range(8)),
                *(''.join(secrets.choice(alphabet) for _ in range(size)) for size in (4,4,4,12))))
            self.client.cookies.set('gdp_user_id',quote(visitor,safe=''),domain='dev.coc.10086.cn',path='/',secure=False)
        session_name='9e4e5fa7244c6b6e_gdp_session_id'
        session_id=self.client.cookies.get(session_name)
        if not session_id:
            session_id=str(uuid4())
            self.client.cookies.set(session_name,session_id,domain='dev.coc.10086.cn',path='/',secure=False)
        if not self.client.cookies.get(session_name+'_sent'):
            self.client.cookies.set(session_name+'_sent',session_id,domain='dev.coc.10086.cn',path='/',secure=False)
        sequence_name='9e4e5fa7244c6b6e_gdp_sequence_ids'
        if not self.client.cookies.get(sequence_name):
            value=quote(json.dumps({'globalKey':149,'VISIT':4,'PAGE':10,'CUSTOM':137},separators=(',',':')),safe='')
            self.client.cookies.set(sequence_name,value,domain='dev.coc.10086.cn',path='/',secure=False)

    def _wheel_cookies(self):
        return [{'name':cookie.name, 'value':cookie.value, 'domain':cookie.domain,
                 'path':cookie.path, 'secure':cookie.secure,
                 'hostOnly':not cookie.domain_specified,
                 'expires':cookie.expires,
                 'httpOnly':str(cookie._rest.get('HttpOnly', cookie._rest.get('http_only', ''))).lower() in ('1', 'true')}
                for cookie in self.client.cookies.jar if not cookie.is_expired()]

    def _apply_wheel_result(self, encrypted):
        if isinstance(encrypted.get('user_agent'), str) and encrypted['user_agent']:
            self.client.headers['User-Agent'] = encrypted['user_agent']
        for cookie in encrypted.get('cookies') or []:
            if (isinstance(cookie, dict) and isinstance(cookie.get('name'), str)
                    and isinstance(cookie.get('value'), str) and cookie.get('domain')):
                path = cookie.get('path') or '/'
                saved_cookie = next((item for item in self.client.cookies.jar
                    if item.name == cookie['name'] and item.domain.lstrip('.') == cookie['domain'].lstrip('.')
                    and item.path == path), None)
                if saved_cookie is None:
                    self.client.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=path)
                    saved_cookie = next(item for item in self.client.cookies.jar
                        if item.name == cookie['name'] and item.domain == cookie['domain'] and item.path == path)
                saved_cookie.value = cookie['value']
                saved_cookie.secure = bool(cookie.get('secure'))
                if 'hostOnly' in cookie:
                    saved_cookie.domain_specified = not cookie['hostOnly']
                    saved_cookie.domain_initial_dot = saved_cookie.domain_specified and saved_cookie.domain.startswith('.')
                if 'expires' in cookie:
                    saved_cookie.expires = int(cookie['expires']) if cookie['expires'] is not None else None
                    saved_cookie.discard = saved_cookie.expires is None
                saved_cookie._rest['http_only'] = str(bool(cookie.get('httpOnly')))
        if (encrypted.get('page_state') and self.credential_cache is not None
                and getattr(self.client, '_credential_binding', None)):
            self.credential_cache.sync(self.client, data={**self.client._credential_data,
                                                        'wheel_page':encrypted['page_state']})

    def _authenticate(self):
        # Cached-session recovery must read today's primary identity, rather
        # than the snapshot captured when the expired activity cookie was made.
        snapshot = self.credentials_provider()
        if snapshot is None or snapshot['phone'] != self.snapshot['phone']:
            raise CocError('共享账号已退出或切换，请重新选择账号', 'APP_LOGIN_REQUIRED', 401)
        if snapshot.get('primary_status') == 'login_required':
            raise CocError('网龄会话已失效，请在账号管理重新登录此号码', 'APP_LOGIN_REQUIRED', 401)
        uid = re.search(r'(?:^|;)\s*UID=([^;]+)', snapshot.get('cookie', ''), re.I)
        if (not re.search(r'(?:^|;)\s*JSESSIONID=', snapshot.get('cookie',''), re.I)
                or not self.app_token_source and uid is None):
            raise CocError('共享账号缺少活动身份，请重新登录', 'APP_LOGIN_REQUIRED', 401)
        self.snapshot = snapshot
        try:
            flow = AppLoginSession.from_credentials(snapshot)
            if self.app_token_source:
                _, result, _ = flow.call('/leadeon-abilityopen-biz/BN/obtainToken/getBigNetToken', {
                    'sourceId': self.app_token_source, 'url': self.entry, 'cellNum': snapshot['phone'],
                })
            else:
                _, result, _ = flow.call('/biz-orange/LN/checkUIDAction/checkUID', {
                    'channelId': '12093', 'domain': ORIGIN, 'url': self.entry,
                    'tel': snapshot['phone'], 'uId': uid[1],
                })
        except AppLoginError as error:
            raise CocError('APP 活动认证暂未完成，请校验并重连', 'APP_UID_FAILED') from error
        code = str(result.get('retCode'))
        if code in APP_SESSION_REJECTION_CODES:
            return self._recover_app_identity(snapshot, code, 'APP_LOGIN_REQUIRED')
        if code != '000000':
            raise CocError('APP 活动认证暂未完成（'+code[:40]+'）', 'APP_UID_FAILED')
        if self.app_token_source:
            body = result.get('rspBody')
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except ValueError as error:
                    raise CocError('APP 活动认证响应格式异常', 'APP_TOKEN_FORMAT') from error
            if not isinstance(body, dict) or not isinstance(body.get('token'), str) or not body['token']:
                raise CocError('APP 未返回官方网龄登录票据', 'APP_TOKEN_FORMAT')
            payload, encryption = {'verifyType':18, 'ext':body['token']}, None
        else:
            payload, encryption = {'uid':encrypt_uid(uid[1]), 'verifyType':1}, 'aurora'
        self.current()
        try:
            self.user = self.post('user/loginCheck', payload, encryption=encryption)
        except CocError as error:
            # These observed loginCheck rejections are not permission to
            # replay a business request carrying the same numeric code.
            if error.code not in ('1','30'):
                raise
            return self._recover_app_identity(snapshot, error.code, 'ACTIVITY_LOGIN_REQUIRED')
        return {'user':self.user}

    def _recover_app_identity(self, snapshot, reason, code):
        if self.app_session_recover is None or self.identity_recovered:
            raise CocError(f'活动登录未通过（{reason}），请重新登录此共享账号后查询。', code, 401)
        self.identity_recovered = True
        try:
            self.app_session_recover(snapshot)
        except AppLoginError as renewal:
            required = isinstance(renewal, AppCredentialError) and renewal.login_required
            raise CocError(str(renewal), 'APP_LOGIN_REQUIRED' if required else 'APP_REFRESH_FAILED',
                           401 if required else 502) from renewal
        self.client.cookies.clear()
        return CocClient._authenticate(self)

    def close(self):
        if self.wheel_runtime is not None:
            self.wheel_runtime.close()
            self.wheel_runtime = None
        if self.client is not None:
            self.client.close()
            self.client = None

    def environment(self, profile='mobile'):
        if profile == 'mobile':
            self._ensure_page_cookies()
        # These are the exact keys written by the official net-age login/page
        # branch before it calls Wheel.g_dt() and Wheel.roto().
        local_storage = {
            'pageId': self.page_id,
            'channelId': self.channel,
            'sellerId': 'undefined',
        }
        for source, target in (
                ('maskedMobile', 'boundMobile'), ('token', 'boundToken'),
                ('provinceCode', 'boundProvinceCode'), ('cityCode', 'boundCityCode'),
                ('userId', 'userId'), ('encryptedData', 'encryptedData')):
            if self.user.get(source) is not None:
                local_storage[target] = str(self.user[source])
        session_storage = {}
        if profile == 'node-page':
            state = getattr(self.client, '_credential_data', {}).get('wheel_page', {})
            if isinstance(state, dict) and state.get('entry') == self.entry:
                local_storage = {**state.get('local_storage', {}), **local_storage}
                session_storage = state.get('session_storage', {})
        visible_cookies = {}
        for cookie in self.client.cookies.jar:
            http_only = str(cookie._rest.get('HttpOnly', cookie._rest.get('http_only', ''))).lower()
            if http_only in ('1', 'true'):
                continue
            visible_cookies[cookie.name] = cookie.value
            if len(visible_cookies) == 5:
                break
        user_agent = self.client.headers['User-Agent']
        collected = collect() if profile == 'truthful' else collect_mobile(user_agent)
        return {**collected, 'wasm_ver': '0.0.3',
                'page_url': self.entry, 'hostname': 'dev.coc.10086.cn',
                'protocol': 'https:', 'port': '', 'referer': self.entry, 'events': '',
                'user_agent': user_agent, 'language': 'zh-CN', 'languages': ['zh-CN'],
                'on_line': True, 'cookie_enabled': True, 'main_script': '',
                'collection_time': int(time.time()*1000),
                'local_storage': local_storage, 'session_storage':session_storage, 'cookie': visible_cookies,
                'tls_fingerprint': {'has_subtle_crypto': False, 'is_secure_context': True,
                                    'protocol': 'https:', 'user_agent_contains_ssl': 'ssl' in user_agent.lower()}}

    def wheel_request(self, payload, environment_profile='mobile', sdk_encrypt=None):
        from .coc_wheel import encrypt, SevenDayCryptoError
        trace_id = ''
        environment = self.environment(environment_profile)
        cookies = self._wheel_cookies()

        def transport(body, headers):
            nonlocal trace_id
            self.current()
            try:
                result = self.client.post(API+'wasmrc/gDT', json=body, headers=headers,
                                          timeout=(8, 20), allow_redirects=False)
            except requests.RequestsError as error:
                raise SevenDayCryptoError('官方设备令牌服务连接失败，本次未提交业务请求。', 'DEVICE_TOKEN_NETWORK') from error
            trace_id = _trace_id(result)
            if result.status_code != 200:
                raise SevenDayCryptoError('官方设备令牌服务 HTTP '+str(result.status_code), 'DEVICE_TOKEN_HTTP')
            try:
                value = result.json()
            except ValueError as error:
                raise SevenDayCryptoError('官方设备令牌响应格式异常', 'DEVICE_TOKEN_FORMAT') from error
            if environment_profile in ('node-page', 'net-age-python'):
                self.last_wheel_exchange['device'] = {
                    'request':body, 'headers':headers, 'response':value, 'trace_id':trace_id,
                }
            return value
        try:
            encrypted = (self.wheel_runtime.encrypt if self.wheel_runtime is not None else sdk_encrypt or self.sdk_encrypt or encrypt)(
                payload, cookies, self.client.headers['User-Agent'], transport=transport,
                environment=environment)
        except SevenDayCryptoError as error:
            message = str(error)
            if trace_id:
                message += f' · 追踪号 {trace_id}'
            failure = CocError(message, error.code, error.status)
            failure.trace_id = trace_id
            raise failure from error
        self._apply_wheel_result(encrypted)
        self.wheel_wire_body = encrypted.get('wire_body')
        calls = (encrypted.get('diagnostics') or {}).get('calls') or []
        context = (encrypted.get('diagnostics') or {}).get('context') or {}
        token_call = calls[-1] if calls else {}
        self.last_wheel_diagnostic = {
            'runtime': (encrypted.get('diagnostics') or {}).get('runtime', 'node-v8'),
            'body_length': len(str((encrypted.get('data') or {}).get('cocEnContent', ''))),
            'aurora_length': len(str((encrypted.get('headers') or {}).get('coc-aurora', ''))),
            'gdt_status': token_call.get('status'),
            'gdt_code': token_call.get('responseCode'),
            'gdt_data_length': token_call.get('responseDataLength'),
            'cookie_count': len(context.get('cookie_parts') or []),
            'local_storage_count': context.get('local_storage_keys'),
            'canvas_length': (context.get('canvas') or {}).get('length'),
            'wheel_reused': context.get('wheel_reused'),
            'wheel_age_ms': context.get('wheel_age_ms'),
            'memory_source': context.get('memory_source'),
            'member_page_checked': context.get('member_page_checked'),
            'clock_source': context.get('clock_source'),
        }
        return encrypted['data'], encrypted['headers']

    def post(self, path, payload, *, encryption=None, mutation=False, before_submit=None, recover=True,
             wheel_environment='mobile', wheel_encrypt=None, request_sender=None):
        key, headers, body = None, {}, payload
        if encryption in ('aurora', 'meteor'):
            body, headers, key = _encrypted(payload, encryption)
        elif encryption == 'wheel':
            body, headers = self.wheel_request(payload, wheel_environment, wheel_encrypt)
        if mutation:
            self.current()
            if before_submit is not None:
                before_submit()
        if encryption == 'wheel' and wheel_environment in ('node-page', 'net-age-python'):
            self.last_wheel_exchange['claim'] = {
                'url':API + path, 'body':self.wheel_wire_body or body,
                'headers':{**dict(self.client.headers), **headers}, 'cookies':self._wheel_cookies(),
            }
        try:
            wire = {'data':self.wheel_wire_body} if encryption == 'wheel' and self.wheel_wire_body is not None else {'json':body}
            response = (request_sender or self.client.post)(API + path, **wire, headers=headers,
                        timeout=(8, 20), allow_redirects=False)
        except requests.RequestsError as error:
            raise CocError('官方连接中断，将先核实结果后再决定后续操作', 'UPSTREAM_NETWORK', uncertain=mutation) from error
        if response.status_code not in (200,401):
            raise CocError('官方接口 HTTP ' + str(response.status_code), 'UPSTREAM_HTTP', uncertain=mutation)
        try:
            value = {'code':'40'} if response.status_code == 401 else response.json()
            if encryption == 'wheel' and wheel_environment in ('node-page', 'net-age-python'):
                self.last_wheel_exchange['claim'].update(
                    status=response.status_code, response=value, response_headers=dict(response.headers))
            return _decode(value, key if encryption == 'meteor' else None)
        except CocError as error:
            error.trace_id = _trace_id(response)
            if error.code in ('40', '1000004000') and self.credential_cache is not None and getattr(self.client,'_credential_binding',None):
                if recover and not mutation and path != 'user/loginCheck' and self.credential_cache.recover(self.client):
                    self.user = self.client._credential_data['user']
                    return self.post(path,payload,encryption=encryption)
                self.credential_cache.invalidate(self.client)
            raise
        except (ValueError, UnicodeError) as error:
            raise CocError('官方返回了非预期内容，将先查询结果', 'UPSTREAM_FORMAT', uncertain=mutation) from error
