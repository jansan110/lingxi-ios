"""China Mobile net-age benefits over the shared COC account session."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException, HTTPSConnection
import json
import re
import secrets
import ssl
from threading import RLock
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import Request

from flask import jsonify, render_template, request

from activities.coc_client import ORIGIN, COC_SCOPE, CocClient, CocError
from activities.coc_slider import slide_position
from activities.net_age_wheel import MOBILE_APP_HEADERS
from activities.net_age_protocol import MOBILE_H5_HEADERS, MEMBER_H5_HEADERS, MEMBER_PROFILE, BIRTHDAY_PROFILE, PythonWheel
from activities.net_age_transport import NetAgeSession
from activities.net_age_member_state import MemberPageState, PREFIX as MEMBER_GIO_PREFIX
from activities.net_age_surprise_protocol import SurpriseWheel, SurpriseProtocolError, GETTOKEN_URL as SURPRISE_GETTOKEN
from feature_runtime import feature_running
from net_age_surprise_scheduler import SurpriseScheduler
from http_transport import requests
from recharge.cmcc import SHANGHAI, mask_phone


PAGE_ID = '1879720710683729920'
CHANNEL = 'P00000001090'
ENTRY = f'{ORIGIN}/coc/web6/ageFeedbackV2/?pageId={PAGE_ID}&channelId={CHANNEL}'
APP_ENTRY = ENTRY + '&iteration=true'
CONFIG_URL = f'https://res.coc.10086.cn/res/cdn/coc1/fixedPath/{PAGE_ID}.js'
FLOW_IDS = ('1920407540822495232', '1920408316635406336', '1920408673400066048')
FLOW_CODES = ('130000006837', '130000006838', '130000006839')
MEMBER_ID = '1920404735896162304'
MEMBER_HISTORY_IDS = (MEMBER_ID, '1917144860071563264')
MEMBER_ALREADY_CLAIMED = '1000007087'
CLAIM_FAILED = '10'
FLOW_BUSY_NOTICE = '官方提示活动太火爆了，请稍后再试'
FLOW_FAILURE_NOTICE = '本次领取失败'
FLOW_PROBE_CODE = '123456'
ENCRYPTION_FAILED = '官方加密参数校验失败（10）'
BIRTHDAY_FAILURE_NOTICE = '官方返回领取失败（10）'
MEMBER_FAILURE_NO_ORDER = ENCRYPTION_FAILED
WASM_PROFILE = 'node-webview-v5'
PYTHON_PROFILE = 'python-net-age-v1'
MEMBER_PYTHON_PROFILE = 'python-net-age-member-v6'
BIRTHDAY_PYTHON_PROFILE = 'python-net-age-birthday-v1'
DEVICE_COOKIES_SCOPE = 'net-age-device-cookies'
AI_IDS = ('1920409168738979840', '1920410150969118720')
BIRTH_IDS = ('1920398686957129728', '1920402448994754560')
BIRTH_COUPONS = (
    ('phone', '话费充值满减券', '130000006303', 5, {str(count) * 6: count for count in range(1, 7)}),
    ('cinema', '电影票抵扣券', '200000043066', 30, {'1111': 1, '2222': 2, '3333': 1, '4444': 2}),
)
SURPRISE_ID = '16096'
SURPRISE_ENTRY = ORIGIN + '/coc3/canvas/rightsmarket-h5-canvas/online/wljxmh?channelCode=P00000122499'
SURPRISE_SCOPE = 'rights-market:net-age-surprise'
SURPRISE_LOTTERY = '/coc3/coc3-market-activity/cmpp-manage/lottery/'
SURPRISE_DRAWS_SCOPE = 'net-age-surprise-draws'
SURPRISE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36')
ORDER_IDS = [*FLOW_IDS, *MEMBER_HISTORY_IDS, *AI_IDS, *BIRTH_IDS]
BADGE_GROUPS = ('member', 'birthday')
BADGE_ORDER_IDS = [*MEMBER_HISTORY_IDS, *BIRTH_IDS]
AVAILABILITY_SCOPE = 'net-age-availability'
RETRY_GROUPS = ('flow', 'member', 'birthday')

# Product-code aliases also appear in order history when an offer is replaced.
AI_OFFERS = (
    ('tools', 'AI 工具', '1920409785622544384', ('200000043068', '200000046591')),
    ('create', 'AI 创作', '1943612386680303616', ('200000043097', '200000043072', '200000046593')),
    ('sport', 'AI 赛事', '1940714322694389760', ('200000043084', '200000034618', '200000046594')),
    ('call', 'AI 通话', '1940714322711166976', ('200000043095', '200000043067', '200000043069', '200000046592')),
)
AI_HIGH_SKUS = ('1920410151036227584', '1943612369417064448', '1940714346484817920', '1940714346509983744')
MEMBER_OFFERS = (
    ('new-call', '玩转新通话', '200000043094', '2102216125856206848', '67136', '1006'),
    ('migu-video', '咪咕视频', '200000043084', '2102310701592678400', '67164', '1007'),
    ('ringtone', '视频彩铃', '200000047332', '2102216125831041024', '67133', '1008'),
    ('migu-game', '咪咕游戏', '200000047333', '2102216125877178368', '67132', '1009'),
    ('cloud', '移动云盘', '200000047331', '2102216125889761280', '67134', '1010'),
    ('iqiyi', '爱奇艺', '200000034555', '1920407016246972416', '58876', '200000034555'),
    ('youku', '优酷视频', '200000034557', '1920407227344568320', '58878', '200000034557'),
    ('mango', '芒果 TV', '200000034564', '1920755073566576640', '59011', '200000034564'),
    ('bilibili', '哔哩哔哩', '200000034558', '2027191931370758144', '63992', '200000034558'),
    ('qq-music', 'QQ 音乐', '200000034571', '1920407115767119872', '58877', '200000034571'),
)
MEMBER_HISTORY_ALIASES = {
    'bilibili': (('2025999800006825',), ('2026483731145236480', '1917144860163837777', '1917144860163837952')),
}


def _order_rows(data, ids):
    grouped = data.get('activityOrderDTO') if isinstance(data, dict) else None
    if grouped is None and isinstance(data, dict) and str(data.get('total')) == '0':
        grouped = {}
    if not isinstance(grouped, dict):
        raise CocError('官方未返回完整领取记录', 'ORDER_FORMAT')
    rows = []
    for activity in ids:
        values = grouped.get(activity) or []
        if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
            raise CocError('官方返回的领取记录格式异常', 'ORDER_FORMAT')
        rows.extend({**row, 'activity_id': activity} for row in values)
    return rows


def _received(orders, offer):
    return any(row['activity_id'] in offer.get('history_ids', (offer['activity'],)) and
               (str(row.get('prizeId')) in offer['prizes'] or str(row.get('skuCode')) in offer['products']) for row in orders)


def _wrong_sms(error):
    return (error.code == CLAIM_FAILED and not any(text in str(error) for text in ('图形','图验','滑块'))
            and any(text in str(error) for text in ('验证码输入有误', '验证码错误'))
            or error.code == '1000009990' and '短信验证码校验失败' in str(error))


def _claim_error_details(error, sensitive_values):
    detail = error.internal_message
    for value in sensitive_values:
        if isinstance(value, str) and len(value) >= 4:
            detail = detail.replace(value, '***')
    detail = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '***', detail)
    # Omit opaque identifiers and free-standing verification codes from the explanation.
    detail = re.sub(r'(?<![A-Za-z0-9])(?:[A-Za-z0-9_+/.=-]{24,}|[0-9]{6})(?![A-Za-z0-9])', '***', detail)
    detail = ' '.join(detail.split())[:200]
    details = {}
    if detail and detail.strip('！!。 ') != '领取失败':
        details['response_detail'] = detail
    if error.trace_id:
        details['trace_id'] = error.trace_id
    return details


def _config():
    try:
        with requests.Session() as session:
            response = session.get(CONFIG_URL, params={'t': int(time.time() // 300)}, timeout=(5, 12))
            response.raise_for_status()
            match = re.search(r'\bconfigData\s*=\s*(\{[^\n]+\})', response.text)
        value = json.loads(match[1]) if match else {}
        if str(value.get('pageId')) != PAGE_ID:
            raise ValueError('unexpected page')
        return {key: item[key] for item in value['configItemList'] for key in item if key.startswith('configContent_')}
    except (requests.RequestsError, ValueError, KeyError, TypeError) as error:
        raise CocError('暂时无法核对官方领取配置，请稍后重试', 'CONFIG_FAILED') from error


@dataclass
class Verification:
    id: str
    client: object
    group: str
    offer: dict
    period: str
    deadline: float
    needs_slider: bool
    ordinate: int = 0
    challenge: str = ''
    access_token: str = ''
    sms_sent_at: float = 0
    sms_attempted_for_slider: bool = False
    retry: bool = False
    probe: bool = False
    probe_attempted: bool = False


class NetAgeClient(CocClient):
    def __init__(self, provider, cache, recover, *, app_auth=False, member=False, birthday=False,
                 device_cookies=None, save_device_cookies=None):
        self.member = member
        self.birthday = birthday
        self.h5 = member or birthday
        app_auth = app_auth or self.h5
        self.device_cookies=device_cookies or []
        self.save_device_cookies=save_device_cookies
        self.member_page_state=None
        self.member_page_checked=False
        super().__init__(provider, entry=APP_ENTRY if app_auth else ENTRY,
                         credential_cache=cache, app_session_recover=recover,
                         session_factory=(lambda:NetAgeSession(member=True)) if self.h5 else
                             NetAgeSession if app_auth else lambda:requests.Session(impersonate='chrome131_android'),
                         credential_scope=COC_SCOPE,
                         credential_aliases=('net-age', 'net-age-ydrz'),
                         app_token_source='056002' if app_auth else None,
                         request_headers=MEMBER_H5_HEADERS if self.h5 else MOBILE_H5_HEADERS if app_auth else MOBILE_APP_HEADERS)

    def connect(self):
        super().connect()
        if self.h5:
            self.member_page_state=None
            self.member_page_checked=False
            self.client.net_age_user_id=str(self.user.get('userId',''))
            self._apply_wheel_result({'cookies':self.device_cookies})
            try:
                response=self.client.get(APP_ENTRY,headers={
                    'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-User':'?1',
                    'Upgrade-Insecure-Requests':'1'},timeout=(5,12),allow_redirects=False)
            except requests.RequestsError as error:
                raise CocError('官方网龄领取页面准备失败，请稍后重试', 'MEMBER_PAGE_NETWORK') from error
            if response.status_code!=200:
                raise CocError('官方网龄领取页面 HTTP '+str(response.status_code), 'MEMBER_PAGE_HTTP')
        return self

    def post(self, path, payload, **kwargs):
        result=super().post(path,payload,**kwargs)
        if self.h5 and path=='user/auth/checkSliderImg':
            # A fresh slider issues a new RiskToken. A previous submission's
            # device envelope must not be paired with this new verification.
            if self.wheel_runtime is not None and self.wheel_runtime.token:
                self.wheel_runtime.close()
                self.wheel_runtime=None
            if self.credential_cache is not None:
                self.credential_cache.sync(self.client)
        return result

    def _ensure_page_cookies(self):
        if not self.h5:
            return super()._ensure_page_cookies()
        if self.member_page_state is not None:
            return
        self.member_page_state=MemberPageState(self.device_cookies,
            self.user.get('maskedMobile') or mask_phone(self.snapshot['phone']))
        retained=[cookie for cookie in self.client.cookies.jar
                  if cookie.name!='gdp_user_id' and not cookie.name.startswith(MEMBER_GIO_PREFIX)]
        self.client.cookies.clear()
        self.device_cookies=self.member_page_state.cookies()
        self._apply_wheel_result({'cookies':self.device_cookies})
        for cookie in retained:
            self.client.cookies.jar.set_cookie(cookie)
        if self.save_device_cookies is not None:
            self.save_device_cookies(self.device_cookies)

    def prepare_wheel(self, *, flow_slide_provinces='', probe=False):
        from activities.coc_wheel import SevenDayCryptoError
        try:
            if not self.h5 and self.wheel_runtime is not None:
                self.wheel_runtime.close()
                self.wheel_runtime=None
            if self.wheel_runtime is None:
                self.wheel_runtime = (PythonWheel(BIRTHDAY_PROFILE if self.birthday else MEMBER_PROFILE,
                    clock=self.client.business_time_ms) if self.h5 else PythonWheel())
            environment = self.environment('mobile')
            if self.h5:
                if not self.member_page_checked:
                    # The official page performs this account-bound query
                    # before its first slider. Doing it after a rejected
                    # submission did not restore the failed session.
                    self.post('activities/growth/query/evaluate',{})
                    self.member_page_checked=True
                    environment=self.environment('mobile')
                environment['local_storage'].update(self.member_page_state.local_storage)
                environment['member_page_checked']=self.member_page_checked
            environment['flow_slide_provinces'] = flow_slide_provinces
            environment['probe'] = probe
            environment['clock_source']=('official-risk-token'
                if self.h5 and self.client._clock_sample is not None else 'local')
            if not self.h5 and self.credential_cache is not None:
                rights = self.credential_cache.load('national-rights-market:member-benefits', self.snapshot)
                token = rights.get('headers', {}).get('phone') if isinstance(rights, dict) else None
                if isinstance(token, str) and token:
                    environment['local_storage']['canvasworkbenchweb_auth'] = json.dumps({
                        'user_info': {'phone':token, 'phoneMask':self.user.get('maskedMobile', '')},
                    }, separators=(',', ':'))
            result = self.wheel_runtime.prepare(self._wheel_cookies, self.client.headers['User-Agent'],
                                                environment)
        except SevenDayCryptoError as error:
            raise CocError(str(error), error.code, error.status) from error
        self._apply_wheel_result(result)

    def orders(self, ids, *, recover=True):
        return _order_rows(self.post('activities/prize/activityOrderListNew', {'activityIds': ids}, recover=recover), ids)

    def python_business_post(self, url, *, headers, timeout, allow_redirects, **wire):
        """Diagnostic transport: gDT stays on the established account session.

        Python's HTTPSConnection does not follow redirects. Certificate and
        hostname validation stay enabled; the current account's cookie jar is
        used unchanged and receives any official Set-Cookie response.
        """
        parsed = urlsplit(url)
        body = wire['data'] if 'data' in wire else json.dumps(wire['json'], separators=(',', ':'))
        body = body.encode('utf-8') if isinstance(body, str) else body
        request = Request(url, data=body, headers={**dict(self.client.headers), **headers}, method='POST')
        self.client.cookies.jar.add_cookie_header(request)
        sent_headers = {**dict(request.header_items()), 'Host':parsed.netloc,
                        'Content-Length':str(len(body)), 'Accept-Encoding':'identity'}
        connection = HTTPSConnection(parsed.hostname, parsed.port or 443,
                                     timeout=timeout[1], context=ssl.create_default_context())
        capture = self.last_wheel_exchange['claim']
        capture.update(transport='python-https-http1', sent_headers=sent_headers)
        try:
            connection.request('POST', parsed.path + ('?'+parsed.query if parsed.query else ''),
                               body=body, headers=sent_headers)
            response = connection.getresponse()
            self.client.cookies.jar.extract_cookies(response, request)
            raw = response.read()
            return SimpleNamespace(status_code=response.status, headers=response.headers,
                                   json=lambda:json.loads(raw))
        except (HTTPException, OSError) as error:
            raise requests.RequestsError('Python 验证通道连接未完成') from error
        finally:
            connection.close()

    def surprise_chances(self):
        data = self.post('activities/prize/remainingQueryTimes', {'activityIds': [SURPRISE_ID]})
        try:
            body = data['contractRoot']['body']
            count = body['data'][0]['remainNum']
            if str(body['resultCode']) == '0' and re.fullmatch(r'\d+', str(count)):
                return int(count)
        except (KeyError, TypeError, IndexError):
            pass
        raise CocError('官方未返回有效的惊喜礼次数', 'SURPRISE_FORMAT')

    def inspect(self, now, *, recover=True, badges_only=False):
        profile = self.post('activities/common/queryUserInfo', {}, recover=recover)
        growth = self.post('activities/growth/query', {'touchCode': self.channel, 'provinceCode': self.user.get('provinceCode', '')},
                           recover=recover)
        level = growth.get('level') if isinstance(growth, dict) else None
        if not isinstance(profile, dict) or not isinstance(level, str):
            raise CocError('官方未返回完整的网龄资格', 'PROFILE_FORMAT')
        orders = self.orders(BADGE_ORDER_IDS if badges_only else ORDER_IDS, recover=recover)
        month = now.strftime('%Y-%m')
        year = now.strftime('%Y-')
        current = [row for row in orders if str(row.get('createTime', '')).startswith(month)]
        annual = [row for row in orders if str(row.get('createTime', '')).startswith(year)]
        rank = int(level[1]) if level in ('V1', 'V2', 'V3', 'V4', 'V5') else 0
        years = re.match(r'(\d+)年', str(profile.get('userChinaMobileage', '')))
        birth_date = str(profile.get('userBegin', ''))
        prior_member_orders = (
            row for row in orders if row['activity_id'] in MEMBER_HISTORY_IDS
            and isinstance(row.get('createTime'), str) and len(row['createTime']) >= 10
            and row['createTime'][:7] < month
        )
        rows = self.benefits(rank, years, profile.get('isBirthMonth'), current, annual,
                             prior_member_orders=prior_member_orders if not badges_only else ())
        if badges_only:
            rows = {group: rows[group] for group in BADGE_GROUPS}
        return {'level': level, 'age': str(profile.get('userChinaMobileage', '')),
                'birthday': f'{birth_date[4:6]}月{birth_date[6:8]}日' if re.fullmatch(r'\d{8}', birth_date) else '',
                'rows': rows}

    def benefits(self, rank, years, birth_month, current, annual, *, prior_member_orders=()):
        flow_tier = 0 if rank < 3 else 1 if rank < 5 else 2
        flow_orders = [row for row in current if row['activity_id'] in FLOW_IDS]
        flow_receipt = max((row for row in flow_orders if str(row.get('skuCode')) in FLOW_CODES),
                           key=lambda row: row.get('createTime', ''), default=None)
        flow_amount = FLOW_CODES.index(str(flow_receipt['skuCode'])) + 1 if flow_receipt else flow_tier + 1
        flow_name = f'{flow_amount}GB 流量月包券'
        flow = self.row(flow_name, rank > 0, flow_receipt is not None, flow_orders, [
            dict(id='monthly', name=flow_name, activity=FLOW_IDS[flow_tier], prizes=(), products=FLOW_CODES,
                 sms_product=FLOW_CODES[flow_tier], mode='sms', history_ids=FLOW_IDS)],
            has_period_orders=bool(flow_orders))
        if flow_orders and flow_receipt is None:
            flow.update(status='结果待确认', description='发现本月官方流量订单，领取内容待核对',
                        available=False, recheck=True)
        member_offers = []
        for key, name, product, sku, right, sms in MEMBER_OFFERS:
            product_aliases, prize_aliases = MEMBER_HISTORY_ALIASES.get(key, ((), ()))
            member_offers.append(dict(id=key, name=name, prizes=(sku, *prize_aliases), products=(product, *product_aliases),
                                      sms_product=sms, sku=sku, right=right, activity=MEMBER_ID,
                                      history_ids=MEMBER_HISTORY_IDS, mode='sms'))
        if self.user.get('provinceCode') in ('898','431','871','531','351','791','471','230'):
            member_offers[2].update(sku='2084941388956573696', right='66096', sms_product='1003',
                                    prizes=('2084941388956573696','2102216125831041024'),
                                    products=('200000034817','200000047332'))
        member_claimed = any(_received(current, offer) for offer in member_offers)
        member_has_order = any(row['activity_id'] in MEMBER_HISTORY_IDS for row in current)
        member = self.row('每月任选 1 款', rank == 5, member_claimed, current, member_offers,
                          unavailable='成长等级 V5 可领', has_period_orders=member_has_order)
        if not member_claimed:
            if member_has_order:
                member.update(status='结果待确认', description='发现本月官方订单，会员名称待核对',
                              available=False, recheck=True)
            latest = max(((order, offer) for order in prior_member_orders for offer in member_offers
                          if _received((order,), offer)), key=lambda match: match[0]['createTime'], default=None)
            if latest:
                order, offer = latest
                member['last_received'] = {'name': order.get('skuName') or offer['name'], 'date': order['createTime'][:10]}
        ai_tier = int(rank >= 3)
        ai_offers = [dict(id=key, name=name, activity=AI_IDS[ai_tier],
                          sku=AI_HIGH_SKUS[index] if ai_tier else sku, prizes=(sku, AI_HIGH_SKUS[index]),
                          products=products, mode='direct', history_ids=AI_IDS)
                     for index, (key, name, sku, products) in enumerate(AI_OFFERS)]
        ai_limit = (1 + ai_tier) if rank else 0
        ai_used = sum(_received(current, offer) for offer in ai_offers)
        birthday_claimed = False
        birthday_ready = bool(years and int(years[1]) >= 1 and birth_month in (True, '1'))
        birthday_offers = []
        for activity, (key, name, sms, amount, counts) in zip(BIRTH_IDS, BIRTH_COUPONS):
            orders = (order for order in annual if order['activity_id'] == activity and str(order.get('skuCode')) in counts)
            latest = max(orders, key=lambda order: order['createTime'], default=None)
            if latest is not None:
                count = counts[str(latest['skuCode'])]
                name = f'{count} 张 {amount} 元{name}'
                minimum = re.search(r'充值\s*(\d+(?:\.\d+)?)\s*元?\s*立减', str(latest.get('skuName', ''))) if key == 'phone' else None
                if minimum:
                    name += f'（满 {minimum[1]} 元可用）'
                birthday_claimed = True
            birthday_offers.append(dict(id=key, name=name, activity=activity, mode='sms', prizes=(),
                                        sms_product=sms, products=tuple(counts)))
        birthday_has_order = any(order['activity_id'] in BIRTH_IDS for order in annual)
        birthday = self.row('生日月任选一款', birthday_ready, birthday_claimed, annual, birthday_offers,
                            unavailable='本月不可领取', has_period_orders=birthday_has_order)
        if birthday_has_order and not birthday_claimed:
            birthday.update(status='结果待确认', description='今年已有官方生日礼订单，领取内容待核对',
                            available=False, recheck=True)
        return {
            'flow': flow,
            'member': member,
            'ai': self.row(f'本月已选 {ai_used}/{ai_limit} 款', rank > 0, bool(ai_limit and ai_used >= ai_limit),
                           current, ai_offers),
            'birthday': birthday,
        }

    @staticmethod
    def row(description, eligible, claimed, orders, offers, *, unavailable='当前账号暂无资格', has_period_orders=False):
        confirmed = [offer for offer in offers if _received(orders, offer)]
        return {'description': description, 'status': '已领取' if claimed else '可领取' if eligible else unavailable,
                'offers': [{**offer, 'received': offer in confirmed} for offer in offers] if eligible or claimed else [],
                'available': eligible and not claimed, 'has_period_orders': has_period_orders}


class NetAge:
    def __init__(self, credentials, accounts, cache, storage, recover, *, now=None):
        self.credentials, self.accounts, self.cache, self.storage, self.recover = credentials, accounts, cache, storage, recover
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.lock = RLock()
        self.account_locks = {}
        self.pending = {}
        self.diagnostics = {}
        self.submissions = (storage.load_activity_data('net-age-submissions') or {}) if storage else {}
        self.availability = (storage.load_activity_data(AVAILABILITY_SCOPE) or {}) if storage else {}
        self.device_cookies=(storage.load_activity_data(DEVICE_COOKIES_SCOPE) or {}) if storage else {}
        self.surprise_draws=(storage.load_activity_data(SURPRISE_DRAWS_SCOPE) or {}) if storage else {}
        self.surprise_wheel_factory=SurpriseWheel

    def save_device_cookies(self, login_id, cookies):
        with self.lock:
            self.device_cookies={**self.device_cookies,login_id:cookies}
            if self.storage is not None:
                self.storage.save_activity_data(DEVICE_COOKIES_SCOPE,self.device_cookies)

    @contextmanager
    def connected(self, login_id):
        snapshot = self.credentials(login_id)
        app_auth = bool(self.cache is not None and snapshot is not None
                        and self.cache.load('net-age-ydrz', snapshot) is not None)
        client = NetAgeClient(lambda: self.credentials(login_id), self.cache, self.recover, app_auth=app_auth)
        try:
            yield client.connect()
        finally:
            client.close()

    def account_lock(self, login_id):
        with self.lock:
            return self.account_locks.setdefault(login_id, RLock())

    @staticmethod
    def period(group, now):
        return now.strftime('%Y' if group == 'birthday' else '%Y-%m')

    def mark(self, login_id, group, record):
        with self.lock:
            pending = {**self.submissions.get(login_id, {})}
            if record is None:
                pending.pop(group, None)
            else:
                pending[group] = record
            updated = {**self.submissions, login_id: pending}
            if self.storage:
                self.storage.save_activity_data('net-age-submissions', updated)
            self.submissions = updated

    def busy_login(self, login_id):
        pending = self.pending.get(login_id)
        return pending is not None and pending.deadline > time.monotonic()

    def close_verification(self, login_id):
        pending = self.pending.pop(login_id, None)
        if pending is not None:
            pending.client.close()

    def record_diagnostic(self, login_id, stage, error):
        self.diagnostics[login_id] = {
            'stage': stage, 'code': error.code, 'message': str(error)[:300],
            'time': self.now().isoformat(),
        }

    def save_crypto_exchange(self, login_id, client, *, probe=False):
        exchange = getattr(client, 'last_wheel_exchange', None)
        if self.storage is not None and isinstance(exchange, dict) and exchange:
            self.storage.save_activity_data('net-age-crypto:'+login_id,
                {'captured_at':self.now().isoformat(), 'diagnostic_only':probe,
                 'wheel_diagnostic':getattr(client, 'last_wheel_diagnostic', {}), 'exchange':exchange})

    @staticmethod
    def _surprise_request(session, path, payload, *, mutation=False, headers=None):
        try:
            response = session.post(ORIGIN + path, json=payload, headers=headers or {}, timeout=(5, 12), allow_redirects=False)
        except requests.RequestsError as error:
            raise CocError('连接官方网龄惊喜礼失败，请稍后核对', 'SURPRISE_NETWORK', uncertain=mutation) from error
        if response.status_code == 401:
            raise CocError('惊喜礼会话已失效', 'SURPRISE_SESSION_EXPIRED', 401)
        if response.status_code == 429:
            raise CocError('官方请求频繁，已暂停自动抽奖', 'SURPRISE_RATE_LIMITED', 429)
        if response.status_code != 200:
            raise CocError(f'官方惊喜礼接口 HTTP {response.status_code}', 'SURPRISE_HTTP', uncertain=mutation)
        try:
            result = response.json()
        except ValueError as error:
            raise CocError('官方惊喜礼响应格式异常', 'SURPRISE_FORMAT', uncertain=mutation) from error
        if not isinstance(result, dict):
            raise CocError('官方惊喜礼响应格式异常', 'SURPRISE_FORMAT', uncertain=mutation)
        return result

    def _authorize_surprise(self, login_id, snapshot, session):
        phone = mask_phone(snapshot['phone'])
        with self.connected(login_id) as activity:
            if activity.user.get('maskedMobile') != phone:
                raise CocError('网龄会话账号与所选号码不一致', 'SURPRISE_ACCOUNT_MISMATCH', 409)
            token = activity.user.get('token')
            if not token:
                raise CocError('官方网龄会话未提供惊喜礼授权', 'SURPRISE_AUTH_REQUIRED', 401)
            activity.post('user/authenticationLogin', {'token': token}, mutation=True)
            result = activity.post('user/getCmPassPortTokenByUid', {'token': token, 'targetSourceId': '056002'}, mutation=True)
        ticket = result.get('cmPassPortToken') if isinstance(result, dict) else None
        if not ticket:
            raise CocError('官方未发放惊喜礼授权凭据', 'SURPRISE_AUTH_REQUIRED', 401)
        result = self._surprise_request(session, '/coc3/gr/login/ssoRights/userLoginV2', {
            'ticket': ticket, 'ticketType': 8, 'businessType': 'RIGHTS_MARKET', 'wxoaType': '',
            'm1': '', 'm2': '', 's1': '', 's2': '', 'from': '',
        })
        if str(result.get('code')) != '0':
            raise CocError('官方惊喜礼授权未通过，请在中国移动 APP 核对登录', 'SURPRISE_AUTH_REQUIRED', 401)
        user = result.get('data')
        if not isinstance(user, dict) or user.get('mobileMask') != phone or not user.get('token'):
            raise CocError('官方惊喜礼账号绑定未通过', 'SURPRISE_ACCOUNT_MISMATCH', 409)
        session.headers['phone'] = user['token']
        return {}

    def _read_surprise(self, session, path, payload):
        def read():
            response = self._surprise_request(session, SURPRISE_LOTTERY + path, payload)
            data = response.get('data')
            code = data.get('resultCode') if isinstance(data, dict) else None
            if str(code) == '-516' or str(response.get('code')) == '-516':
                raise CocError('惊喜礼会话已失效', 'SURPRISE_SESSION_EXPIRED', 401)
            if str(response.get('code')) != '200' or code not in (None, 0, '0'):
                raise CocError('官方尚未返回惊喜礼查询结果', 'SURPRISE_FAILED')
            return data

        try:
            return read()
        except CocError as error:
            if error.code != 'SURPRISE_SESSION_EXPIRED' or not self.cache.recover(session):
                raise
        return read()

    @contextmanager
    def _surprise_session(self, login_id):
        snapshot = self.credentials(login_id)
        if snapshot is None:
            raise CocError('请先恢复该共享账号登录', 'APP_LOGIN_REQUIRED', 401)
        with requests.Session(impersonate='chrome150',default_headers=False) as session:
            session.headers.update({'Origin': ORIGIN, 'Referer': SURPRISE_ENTRY,
                'User-Agent':SURPRISE_UA,'Accept':'application/json, text/plain, */*',
                'Content-Type':'application/json;charset=UTF-8','Accept-Language':'zh-CN,zh;q=0.9'})
            self.cache.connect(SURPRISE_SCOPE, snapshot, session,
                               lambda: self._authorize_surprise(login_id, snapshot, session), headers=('phone',))
            try:
                yield snapshot,session
            finally:
                self.cache.sync(session)

    def _surprise_snapshot(self, session):
        remaining = self._read_surprise(session, 'residueLotteryCount', {'activityId': int(SURPRISE_ID)})
        if type(remaining) is not int or remaining < 0:
            raise CocError('官方抽奖次数格式异常', 'SURPRISE_FORMAT')
        awards = self._read_surprise(session, 'lotteryList', {'activityIds': [int(SURPRISE_ID)],
                                                             'pageIndex': 1, 'pageSize': 10, 'lotteryFlag': False})
        if not isinstance(awards, dict) or not (isinstance(awards.get('list'), list)
                                                or awards.get('list') is None and str(awards.get('total')) == '0'):
            raise CocError('官方奖品记录格式异常', 'SURPRISE_FORMAT')
        prizes = [{'name': str(item.get('showName') or item.get('salesUnitName'))[:120],
                   'date': str(item.get('createTime') or '')[:30]}
                  for item in awards.get('list') or [] if isinstance(item, dict) and str(item.get('salesUnitId')) != '-1'
                  and (item.get('showName') or item.get('salesUnitName'))]
        return {'remaining':remaining,'prizes':prizes,
                'record_ids':{str(item['id']) for item in awards.get('list') or [] if isinstance(item,dict) and item.get('id') is not None}}

    def surprise_details(self, login_id):
        with self.account_lock(login_id),self._surprise_session(login_id) as (_,session):
            state=self._surprise_snapshot(session)
            return {key:state[key] for key in ('remaining','prizes')}

    def _save_surprise_draw(self, login_id, record):
        with self.lock:
            updated={**self.surprise_draws,login_id:record}
            if self.storage:self.storage.save_activity_data(SURPRISE_DRAWS_SCOPE,updated)
            self.surprise_draws=updated

    def surprise_progress(self, login_id):
        """Read confirmed local progress, including draws before a later failure."""
        with self.lock:
            saved=deepcopy(self.surprise_draws.get(login_id,{}))
        day=self.now().date().isoformat()
        if not str(saved.get('checked_at','')).startswith(day):return {}
        return {'remaining':saved.get('remaining'),'prizes':saved.get('prizes',[]),
            'draws':[outcome for outcome in saved.get('outcomes',[])
                if str(outcome.get('confirmed_at','')).startswith(day)]}

    @staticmethod
    def _surprise_outcome(response):
        data=response.get('data')
        sales=data.get('activitySales') if isinstance(data,dict) else None
        record=data.get('lotteryRecord') if isinstance(data,dict) else None
        if not isinstance(sales,dict) or not isinstance(record,dict) or record.get('id') is None:
            raise CocError('官方未返回完整抽奖回执，已停止后续抽奖','SURPRISE_RESULT_UNCERTAIN',uncertain=True)
        unit=str(sales.get('salesUnitId'))
        if unit not in ('-1','-2','-3') and not unit.isdigit():
            raise CocError('官方奖品类型暂无法确认，已停止后续抽奖','SURPRISE_RESULT_UNCERTAIN',uncertain=True)
        return {'record_id':str(record['id']),'won':unit!='-1',
            'name':str(sales.get('showName') or sales.get('salesUnitName') or ('未中奖' if unit=='-1' else '奖品信息待核对'))[:120],
            'date':str(record.get('createTime') or '')[:30],'needs_address':unit=='-2','confirmed':False}

    def auto_surprise(self, login_id):
        """Use only confirmed available chances; never replay an uncertain draw."""
        with self.account_lock(login_id):
            if self.busy_login(login_id):
                raise CocError('该账号正在验证网龄权益，请完成或取消后再抽奖','ACCOUNT_BUSY',409)
            with self._surprise_session(login_id) as (snapshot,session):
                state=self._surprise_snapshot(session)
                saved=deepcopy(self.surprise_draws.get(login_id,{}))
                pending=saved.get('pending')
                draws=[]
                if pending:
                    outcome=pending.get('outcome')
                    if (outcome and outcome['record_id'] in state['record_ids']
                            and state['remaining']<pending['before_remaining']):
                        outcome={**outcome,'confirmed':True,'confirmed_at':self.now().isoformat()}
                        draws.append(outcome)
                        saved.setdefault('outcomes',[]).append(outcome)
                        saved['pending']=None
                        self._save_surprise_draw(login_id,saved)
                    else:
                        raise CocError('上次抽奖结果仍待官方核对，未重复提交','SURPRISE_RESULT_UNCERTAIN',uncertain=True)
                initial=state['remaining']
                saved.update(remaining=state['remaining'],prizes=state['prizes'],checked_at=self.now().isoformat())
                wheel=None
                for _ in range(initial):
                    if state['remaining']==0:break
                    if not feature_running():raise CocError('网龄已关闭，抽奖已停止','FEATURE_DISABLED',423)
                    current=self.credentials(login_id)
                    if current is None or current['phone']!=snapshot['phone']:
                        raise CocError('账号已退出或切换，本次不再提交抽奖','APP_CREDENTIALS_CHANGED',409)
                    if wheel is None or wheel.token!=session.headers['phone']:
                        cookies=[{'name':c.name,'value':c.value,
                            'httpOnly':str(c._rest.get('http_only','')).lower() in ('true','1') or
                                'http_only' not in c._rest and c.has_nonstandard_attr('HttpOnly')}
                            for c in session.cookies.jar if not c.is_expired()]
                        wheel=self.surprise_wheel_factory(session.headers['phone'],SURPRISE_UA,SURPRISE_ENTRY,
                            storage=self.storage,cookies=cookies)
                    def device_request(body,headers):
                        return self._surprise_request(session,SURPRISE_GETTOKEN[len(ORIGIN):],body,headers=headers)
                    try:
                        signed=wheel.sign(transport=device_request)
                    except SurpriseProtocolError as error:
                        raise CocError(str(error),error.code,error.status) from error
                    headers={'wasmParams':json.dumps(signed['wasm_params'],separators=(',',':')),'gr-risk-p3':''}
                    payload={'activityId':int(SURPRISE_ID),'channelNo':'P00000122499','referer':'wljxmh','operDfp':None}
                    pending={'attempt_id':secrets.token_hex(16),'created_at':self.now().isoformat(),
                             'before_remaining':state['remaining'],'outcome':None}
                    saved['pending']=pending
                    self._save_surprise_draw(login_id,saved)
                    response=self._surprise_request(session,SURPRISE_LOTTERY+'lottery',payload,mutation=True,headers=headers)
                    code=str(response.get('code'))
                    data=response.get('data')
                    if code!='60000':
                        if response.get('code') is None or code in ('0','200'):
                            raise CocError('官方抽奖回执无法确认，未重复提交','SURPRISE_RESULT_UNCERTAIN',uncertain=True)
                        # An explicit rejection is not an uncertain submission.
                        saved['pending']=None
                        self._save_surprise_draw(login_id,saved)
                        if code=='-516':
                            self.cache.invalidate(session)
                            raise CocError('惊喜礼登录已失效，本次未重放抽奖','SURPRISE_SESSION_EXPIRED',401)
                        if isinstance(data,dict) and data.get('risk'):
                            raise CocError('官方要求进一步验证，自动抽奖已暂停','SURPRISE_VERIFICATION_REQUIRED',409)
                        if code=='-105':raise CocError('官方提示抽奖过于频繁，已暂停','SURPRISE_RATE_LIMITED',429)
                        if code=='-60001':raise CocError('官方活动未在进行中','ACTIVITY_CLOSED',409)
                        if code in ('-70039','-61014','-61015','-16'):
                            raise CocError('官方确认当前暂无可抽取的奖品，自动任务已暂停','SURPRISE_INELIGIBLE',409)
                        raise CocError('官方未接受本次抽奖（'+code[:32]+'），自动任务已暂停','SURPRISE_REJECTED',409)
                    outcome=self._surprise_outcome(response)
                    saved['pending']={**pending,'outcome':outcome}
                    self._save_surprise_draw(login_id,saved)
                    after=self._surprise_snapshot(session)
                    if after['remaining']>=state['remaining'] or outcome['record_id'] not in after['record_ids']:
                        raise CocError('官方已返回抽奖回执，次数或奖品记录尚未同步；未继续提交','SURPRISE_RESULT_UNCERTAIN',uncertain=True)
                    outcome={**outcome,'confirmed':True,'confirmed_at':self.now().isoformat()}
                    draws.append(outcome)
                    saved.setdefault('outcomes',[]).append(outcome)
                    saved.update(pending=None,remaining=after['remaining'],prizes=after['prizes'],checked_at=self.now().isoformat())
                    self._save_surprise_draw(login_id,saved)
                    state=after
                    if state['remaining']>0:
                        if not feature_running():raise CocError('网龄已关闭，后续抽奖已停止','FEATURE_DISABLED',423)
                        time.sleep(1)
                saved.update(remaining=state['remaining'],prizes=state['prizes'],checked_at=self.now().isoformat())
                self._save_surprise_draw(login_id,saved)
                return {'remaining':state['remaining'],'draws':draws,'prizes':state['prizes'],
                    'message':('今日已核对，暂无可用抽奖次数。' if not draws else f'已核对完成 {len(draws)} 次抽奖，剩余 {state["remaining"]} 次。')}

    def _save_availability(self, login_id, record):
        if record == self.availability.get(login_id):
            return
        updated = {**self.availability, login_id: record}
        if self.storage:
            self.storage.save_activity_data(AVAILABILITY_SCOPE, updated)
        self.availability = updated

    def available_counts(self, login_ids):
        now = self.now()
        month = now.strftime('%Y-%m')
        with self.lock:
            return {
                login_id: sum(
                    self.submissions.get(login_id, {}).get(group, {}).get('period') != self.period(group, now)
                    for group in record['groups']
                )
                for login_id, record in self.availability.items()
                if login_id in login_ids and record.get('month') == month and 'groups' in record
            }

    def check_availability(self, login_id):
        now = self.now()
        day = now.date().isoformat()
        with self.lock:
            record = self.availability.get(login_id, {})
            if record.get('attempt_day') == day or self.busy_login(login_id):
                return self.available_counts({login_id}).get(login_id)
            self._save_availability(login_id, {**record, 'attempt_day': day})
        with self.connected(login_id) as client:
            rows = client.inspect(now, badges_only=True)['rows']
            return self._record_availability(login_id, now, rows)

    @staticmethod
    def _can_prepare_again(group, row, record):
        if group not in RETRY_GROUPS or row['has_period_orders'] or record.get('accepted'):
            return False
        code = record.get('response_code')
        if group == 'birthday':
            return code in (CLAIM_FAILED, '1000008051')
        if group == 'flow':
            return code != MEMBER_ALREADY_CLAIMED
        if code == CLAIM_FAILED:
            return group == 'member' and record.get('wasm_profile') != MEMBER_PYTHON_PROFILE
        return code != MEMBER_ALREADY_CLAIMED

    def _record_availability(self, login_id, now, rows):
        with self.lock:
            available = [group for group in BADGE_GROUPS if rows[group]['available']]
            for group, row in rows.items():
                record = self.submissions.get(login_id, {}).get(group)
                if record and record['period'] == self.period(group, now):
                    if any(offer['received'] and (group in ('flow', 'member', 'birthday') or offer['id'] == record['choice'])
                           for offer in row['offers']):
                        self.mark(login_id, group, None)
                    elif group == 'member':
                        if record.get('accepted'):
                            name = next(name for key, name, *_ in MEMBER_OFFERS if key == record['choice'])
                            row.update(status='官方已受理', description=f'{name}领取记录待同步', recheck=True, available=False)
                        else:
                            status = '结果待确认'
                            reason = '本月暂未查到会员领取订单'
                            if row['has_period_orders']:
                                reason = '发现本月官方订单，会员名称待核对'
                            elif record.get('response_code') == MEMBER_ALREADY_CLAIMED:
                                reason = '官方提示请勿重复领取，会员记录待核对'
                            elif record.get('response_code') == CLAIM_FAILED:
                                status, reason = '上次领取失败', MEMBER_FAILURE_NO_ORDER
                                if detail := record.get('response_detail'):
                                    reason += f' · 官方说明 {detail}'
                                if trace_id := record.get('trace_id'):
                                    reason += f' · 追踪号 {trace_id}'
                            row.update(status=status, description=reason, recheck=True, available=False,
                                       retry_available=row['available'] and self._can_prepare_again(group, row, record))
                    elif group == 'flow':
                        has_orders = row['has_period_orders']
                        status = '结果待确认'
                        description = '已核对官方订单，本月暂未查到领取记录'
                        if has_orders:
                            description = row['description']
                        elif record.get('response_busy'):
                            status = '活动繁忙'
                            description = f'{FLOW_BUSY_NOTICE}。本月暂未查到领取记录'
                        elif record.get('response_code') == CLAIM_FAILED:
                            status = FLOW_FAILURE_NOTICE
                            description = ''
                        row.update(status=status, description=description,
                                   retry_available=row['available'] and self._can_prepare_again(group, row, record),
                                   recheck=True, available=False)
                    elif group == 'birthday':
                        status = '结果待确认'
                        description = '已查询官方订单，暂未查到领取记录'
                        if row['has_period_orders']:
                            description = row['description']
                        elif record.get('response_code') == MEMBER_ALREADY_CLAIMED:
                            description = '官方提示请勿重复领取，生日礼记录待核对'
                        elif record.get('response_code') == CLAIM_FAILED:
                            status = '上次领取失败'
                            description = BIRTHDAY_FAILURE_NOTICE
                            if detail := record.get('response_detail'):
                                description += f' · 官方说明 {detail}'
                            if trace_id := record.get('trace_id'):
                                description += f' · 追踪号 {trace_id}'
                        row.update(status=status, description=description, available=False, recheck=True,
                                   retry_available=row['available'] and self._can_prepare_again(group, row, record))
                    else:
                        row.update(status='结果待核对', available=False)
            previous = self.availability.get(login_id, {})
            stamp = now.isoformat()
            if stamp >= previous.get('checked_at', ''):
                self._save_availability(login_id, {'month': now.strftime('%Y-%m'), 'checked_at': stamp,
                    'attempt_day': max(now.date().isoformat(), previous.get('attempt_day', '')), 'groups': available})
            return self.available_counts({login_id}).get(login_id, 0)

    def status(self, login_id, client):
        now = self.now()
        value = client.inspect(now)
        value['available_count'] = self._record_availability(login_id, now, value['rows'])
        for row in value['rows'].values():
            row['offers'] = [{key: offer[key] for key in ('id', 'name', 'received', 'mode')} for offer in row['offers']]
        try:
            remaining = client.surprise_chances()
            surprise = {'status': f'专区剩余 {remaining} 次', 'section_remaining': remaining,
                        'description': '后台每日核对全部有效账号，按官方抽奖接口的实际可用次数自动抽取'}
        except CocError as error:
            surprise = {'status': '次数待查询', 'description': str(error)}
        value['rows']['surprise'] = surprise
        return value

    def _offer(self, client, login_id, group, choice, now, *, recover=True, retry=False):
        row = client.inspect(now, recover=recover)['rows'][group]
        offer = next((item for item in row['offers'] if item['id'] == choice and not item['received']), None)
        if not row['available'] or offer is None:
            raise CocError('当前选项不可领取，请刷新官方状态', 'NOT_AVAILABLE', 409)
        pending = self.submissions.get(login_id, {}).get(group, {})
        if (pending.get('period') == self.period(group, now)
                and not (retry and self._can_prepare_again(group, row, pending))):
            raise CocError('该权益有一笔结果待核对，请先刷新', 'SUBMISSION_PENDING', 409)
        client.post('activities/growth/verify/status', {}, recover=recover)
        return offer

    def begin(self, login_id, group, choice, *, retry=False, probe=False):
        if probe and group not in ('flow', 'member', 'birthday'):
            raise CocError('当前权益不支持不发短信验证', 'PROBE_UNSUPPORTED', 400)
        with self.account_lock(login_id):
            if self.busy_login(login_id):
                raise CocError('该账号有一笔正在验证的领取，请先完成或取消', 'ACCOUNT_BUSY', 409)
            self.close_verification(login_id)
            client = NetAgeClient(lambda: self.credentials(login_id), self.cache, self.recover,
                                  app_auth=group in ('flow', 'member', 'birthday'), **({
                                      'member':group=='member','birthday':group=='birthday',
                                      'device_cookies':self.device_cookies.get(login_id,[]),
                                      'save_device_cookies':lambda cookies:self.save_device_cookies(login_id,cookies)
                                  } if group in ('member','birthday') else {}))
            try:
                client.connect()
                now = self.now()
                offer = self._offer(client, login_id, group, choice, now, retry=retry or probe)
                if offer['mode'] == 'direct':
                    return self._submit(client, login_id, group, offer, self.period(group, now))
                needs_slider = group == 'member' or offer['activity'] == BIRTH_IDS[1]
                if group == 'flow':
                    provinces = str(_config().get('configContent_27', '')).split(',')
                    needs_slider = client.user.get('provinceCode') in {province.strip() for province in provinces}
                    if probe and needs_slider:
                        raise CocError('当前账号需要官方滑块，请先完成正常验证', 'SLIDER_REQUIRED', 409)
                    client.prepare_wheel(flow_slide_provinces=','.join(provinces), probe=probe)
                elif group in ('member','birthday'):
                    client.prepare_wheel()
                operation = Verification(secrets.token_urlsafe(18), client, group, offer, self.period(group, now),
                                         time.monotonic()+600, needs_slider, retry=retry, probe=probe)
                self.pending[login_id] = operation
                return {'operation': operation.id, 'needs_slider': needs_slider, 'offer': offer['name'], 'probe':probe,
                        'notice': '请确认当前手机号已绑定 QQ 音乐账号。领取会使用本月唯一名额。'
                                  if offer['id'] == 'qq-music' else ''}
            finally:
                if login_id not in self.pending:
                    client.close()

    def _slider_challenge(self, operation):
        client=operation.client
        data=client.post('user/auth/getSliderImg',{'mobilePhone':client.user.get('userId','')},mutation=True)
        if not isinstance(data,dict) or not all(data.get(key) for key in ('ground','patch','authToken')):
            raise CocError('官方没有返回可用的滑块验证','SLIDER_FORMAT')
        if not re.fullmatch(r'[0-9]{1,3}',str(data.get('ordinate'))):
            raise CocError('官方滑块位置格式异常','SLIDER_FORMAT')
        operation.ordinate=int(data['ordinate'])
        operation.challenge=data['authToken']
        operation.access_token=''
        return data

    def _check_slider(self, operation, position):
        if not operation.challenge:
            raise CocError('请先获取官方滑块','SLIDER_REQUIRED',409)
        token,operation.challenge=operation.challenge,''
        data=operation.client.post('user/auth/checkSliderImg',{'authToken':token,
            'ordinate':operation.ordinate,'abscissa':position},mutation=True)
        access=(data.get('accessToken') or data.get('token') or data.get('authToken')) if isinstance(data,dict) else None
        if not access:
            raise CocError('官方滑块验证凭证缺失，请重新验证','SLIDER_FORMAT')
        operation.access_token=access
        return {'message':'官方图形验证通过'}

    def _auto_slider(self, operation):
        data=self._slider_challenge(operation)
        position=slide_position(data['ground'],data['patch'])
        return self._check_slider(operation,position)

    def _check_member_orders(self, operation):
        orders=operation.client.orders(MEMBER_HISTORY_IDS,recover=False)
        if any(str(order.get('createTime') or '').startswith(operation.period) for order in orders):
            raise CocError('本月已有官方会员领取记录，请刷新核对','NOT_AVAILABLE',409)

    def _check_birthday_orders(self, operation):
        orders=operation.client.orders(BIRTH_IDS,recover=False)
        if any(str(order.get('createTime') or '').startswith(operation.period) for order in orders):
            raise CocError('今年已有官方生日礼领取记录，请刷新核对','NOT_AVAILABLE',409)

    def verification(self, login_id, operation_id, action, payload):
        with self.account_lock(login_id):
            operation = self.pending.get(login_id)
            if operation is None or operation.id != operation_id:
                raise CocError('请重新选择要领取的权益', 'VERIFICATION_MISSING', 409)
            if action == 'cancel' or not self.busy_login(login_id):
                self.close_verification(login_id)
                if action == 'cancel':
                    return {'ok': True}
                raise CocError('领取确认已超时，请重新选择权益', 'VERIFICATION_EXPIRED', 409)
            client = operation.client
            if action == 'probe':
                if not operation.probe:
                    raise CocError('请先选择仅验证模式', 'PROBE_REQUIRED', 409)
                if operation.period != self.period(operation.group, self.now()):
                    self.close_verification(login_id)
                    raise CocError('权益领取周期已变更，请重新查询', 'PERIOD_CHANGED', 409)
                transport = payload.get('transport', 'current')
                if transport not in ('current', 'python'):
                    raise CocError('请选择验证通道', 'PROBE_TRANSPORT', 400)
                if operation.group=='birthday' and not operation.needs_slider and transport!='current':
                    raise CocError('话费券请使用默认验证通道','PROBE_TRANSPORT',400)
                if operation.group in ('member','birthday'):
                    if operation.group=='member':self._check_member_orders(operation)
                    else:self._check_birthday_orders(operation)
                    if operation.needs_slider and not operation.access_token:
                        self._auto_slider(operation)
                elif operation.needs_slider and not operation.access_token:
                    raise CocError('请先完成官方滑块验证', 'SLIDER_REQUIRED', 409)
                return self.probe_flow(login_id, operation, transport=transport)
            if operation.probe and action not in ('challenge', 'slider', 'auto-slider'):
                raise CocError('仅验证模式不发送短信，也不提交真实验证码', 'PROBE_ONLY', 400)
            fresh_slider = operation.needs_slider and operation.group in ('member', 'birthday')
            if (action == 'sms' or (fresh_slider and action in ('challenge','auto-slider'))) and operation.sms_sent_at:
                if time.monotonic() - operation.sms_sent_at < 60:
                    raise CocError('请等待 60 秒后重新获取验证码', 'SMS_COOLDOWN', 409)
            if action in ('challenge','auto-slider'):
                operation.sms_attempted_for_slider = False
                if action=='auto-slider':
                    return self._auto_slider(operation)
                data=self._slider_challenge(operation)
                return {'ground': data['ground'], 'patch': data['patch'], 'ordinate': operation.ordinate}
            if action == 'slider':
                return self._check_slider(operation,payload['position'])
            if action == 'sms':
                if operation.needs_slider and not operation.access_token:
                    raise CocError('请先完成官方滑块验证','SLIDER_REQUIRED',409)
                if fresh_slider and operation.sms_attempted_for_slider:
                    raise CocError('请重新完成官方滑块验证后获取验证码', 'SLIDER_REQUIRED', 409)
                offer = operation.offer

                def record_sms_attempt():
                    operation.sms_sent_at = time.monotonic()
                    operation.sms_attempted_for_slider = operation.needs_slider

                touch_code = client.channel if isinstance(getattr(client, 'channel', None), str) else CHANNEL
                client.post('user/smsCode', {'mobile': client.user.get('maskedMobile', ''), 'userId': client.user.get('userId', ''),
                            'productCode': offer['sms_product'], 'pageId': PAGE_ID, 'touchCode': touch_code,
                            'smsCodeTypeEnum': '505'}, mutation=True,
                            before_submit=record_sms_attempt)
                return {'message': '官方已发送验证码'}
            if not operation.sms_sent_at or (fresh_slider and not operation.sms_attempted_for_slider):
                raise CocError('请先获取官方短信验证码', 'SMS_REQUIRED', 409)
            if operation.group!='member' and operation.needs_slider and not operation.access_token:
                raise CocError('请先完成官方滑块验证','SLIDER_REQUIRED',409)
            now = self.now()
            if operation.period != self.period(operation.group, now):
                self.close_verification(login_id)
                raise CocError('权益领取周期已变更，请重新查询', 'PERIOD_CHANGED', 409)
            # Monthly flow binds the inspected offer and initialized Wheel before
            # SMS. The official submit goes straight to gDT/claim in that session.
            offer = operation.offer
            keep_session=False
            prepared=False
            try:
                if operation.group not in ('flow','member'):
                    offer=self._offer(client,login_id,operation.group,operation.offer['id'],now,
                                      recover=False,retry=operation.retry)
                    if offer!=operation.offer:
                        raise CocError('官方权益已更新，请重新选择','OFFER_CHANGED',409)
                if operation.group == 'member':
                    self._check_member_orders(operation)
                    if not operation.access_token:
                        self._auto_slider(operation)
                    client.prepare_wheel()
                elif operation.group == 'birthday':
                    client.prepare_wheel()
                access,operation.access_token=operation.access_token,''
                prepared=True
                return self._submit(client, login_id, operation.group, offer, operation.period,
                                    code=payload['code'], access=access)
            except CocError as error:
                keep_session=operation.group=='member' and not error.uncertain and error.status not in (401,409,423) and (
                    not prepared or error.code in ('1000008051','GDT_REJECTED') or _wrong_sms(error))
                raise
            finally:
                if not keep_session:
                    self.close_verification(login_id)

    def probe_flow(self, login_id, operation, *, transport='current'):
        client, offer = operation.client, operation.offer
        if operation.probe_attempted or operation.group in ('member','birthday'):
            client.prepare_wheel(probe=True)
        operation.probe_attempted = True
        payload = {'lotteryId':offer['activity'], 'provinceCode':client.user.get('provinceCode', ''),
                   'jsonExtParam':json.dumps({'smsCode':FLOW_PROBE_CODE,
                       'touchCode':client.channel, 'productCode':offer['sms_product']}, separators=(',', ':'))}
        if operation.group == 'member':
            payload = {'lotteryId':offer['activity'], 'jsonExtParam':json.dumps({
                'activitySkuId':offer['sku'],'touchCode':client.channel,'smsCode':FLOW_PROBE_CODE,
                'productCode':offer['sms_product'],'rightSkuId':offer['right'],
                'accessToken':operation.access_token},separators=(',',':')),
                'provinceCode':client.user.get('provinceCode','')}
        wheel=True
        path='activities/prize/templatePrize/new'
        if operation.group=='birthday':
            wheel=offer['activity']!=BIRTH_IDS[0]
            extra={'smsCode':FLOW_PROBE_CODE,'productCode':offer['sms_product'],'touchCode':client.channel}
            if operation.needs_slider:extra['accessToken']=operation.access_token
            payload={'lotteryId' if wheel else 'activityId':offer['activity'],
                'jsonExtParam':json.dumps(extra,separators=(',',':')),'provinceCode':client.user.get('provinceCode','')}
            if not wheel:path='activities/prize/templatePrize'
        record = {'choice':offer['id'], 'period':operation.period, 'diagnostic_only':True}
        submitted = False

        def before_submit():
            nonlocal submitted
            if not feature_running():
                raise CocError('网龄已关闭，验证已停止', 'FEATURE_DISABLED', 423)
            submitted = True
            if operation.needs_slider:
                operation.access_token = ''
                operation.challenge = ''
            record['submitted_at'] = self.now().isoformat()

        try:
            if operation.group in ('member','birthday'):
                client.last_wheel_exchange['verification'] = {'request_payload':payload,
                    'path':path,
                    'transport_profile':getattr(client.client,'net_age_transport_profile',None)}
            client.post(path, payload, encryption='wheel' if wheel else 'aurora', mutation=True,
                        recover=False, before_submit=before_submit, wheel_environment='net-age-python',
                        request_sender=client.python_business_post if transport == 'python' and wheel else None)
        except CocError as error:
            if error.uncertain:
                if submitted:
                    self.mark(login_id, operation.group, {**record, 'response_code':error.code})
                self.close_verification(login_id)
                raise
            if error.status in (401, 409, 423):
                self.close_verification(login_id)
                raise
            passed = _wrong_sms(error)
            refresh_slider = operation.needs_slider and submitted
            return {'diagnostic_only':True, 'confirmed':False, 'passed':passed, 'code':error.code,
                    'message':('接口验证通过：' if passed else '接口验证未通过：')+str(error),
                    'trace_id':error.trace_id, 'transport':transport,'refresh_slider':refresh_slider}
        else:
            # Even a declared wrong test code must never turn an unexpected
            # acceptance into a claimed-success label or another submission.
            self.mark(login_id, operation.group, record)
            self.close_verification(login_id)
            return {'diagnostic_only':True, 'confirmed':False, 'stop':True,
                    'message':'官方返回受理，已停止验证，请核对订单'}
        finally:
            self.save_crypto_exchange(login_id, client, probe=True)

    def _submit(self, client, login_id, group, offer, period, *, code='', access=''):
        touch_code = client.channel if isinstance(getattr(client, 'channel', None), str) else CHANNEL
        if group == 'member':
            extra = {'activitySkuId': offer['sku'], 'touchCode': touch_code, 'smsCode': code,
                     'productCode': offer['sms_product'], 'rightSkuId': offer['right'], 'accessToken': access}
        elif group == 'birthday' and code:
            extra = {'smsCode': code, 'productCode': offer['sms_product'], 'touchCode': touch_code}
            if access:
                extra['accessToken'] = access
        elif code:
            extra = {'smsCode': code, 'touchCode': touch_code, 'productCode': offer['sms_product']}
            if access:
                extra['accessToken'] = access
        else:
            extra = {}
            if offer.get('sku'):
                extra['activitySkuId'] = offer['sku']
            extra['touchCode'] = touch_code
            if offer.get('right'):
                extra['rightSkuId'] = offer['right']
            if access:
                extra['accessToken'] = access
        wheel = bool(code) and offer['activity'] != BIRTH_IDS[0]
        path = 'activities/prize/templatePrize' + ('/new' if wheel else '')
        if wheel and group == 'flow':
            payload = {'lotteryId': offer['activity'], 'provinceCode': client.user.get('provinceCode', ''),
                       'jsonExtParam': json.dumps(extra, separators=(',', ':'))}
        elif wheel:
            payload = {'lotteryId': offer['activity'], 'jsonExtParam': json.dumps(extra, separators=(',', ':')),
                       'provinceCode': client.user.get('provinceCode', '')}
        else:
            payload = {'activityId': offer['activity'], 'provinceCode': client.user.get('provinceCode', ''),
                       'jsonExtParam': json.dumps(extra, separators=(',', ':'))}
        previous = self.submissions.get(login_id, {}).get(group)
        record = {'choice': offer['id'], 'period': period}
        wheel_environment = 'net-age-python' if group in ('flow', 'member', 'birthday') else 'mobile'
        if group == 'flow':
            record['business_request'] = {
                'method': 'POST',
                'offer_name': offer['name'],
                'activity_id': offer['activity'],
                'product_code': extra.get('productCode'),
                'channel_id': touch_code,
                'province_code': payload['provinceCode'],
            }
            transport_profile = getattr(client.client, 'net_age_transport_profile', None)
            if not isinstance(transport_profile, str):
                transport_profile = getattr(client.client, 'impersonate', None)
            if isinstance(transport_profile, str):
                record['business_request']['transport_profile'] = transport_profile
        if wheel:
            record['wasm_profile'] = (MEMBER_PYTHON_PROFILE if group=='member' else
                BIRTHDAY_PYTHON_PROFILE if group=='birthday' else PYTHON_PROFILE if group=='flow' else WASM_PROFILE)
            record['wheel_environment'] = wheel_environment
        if group in ('member','birthday'):
            client.last_wheel_exchange={'verification':{
                'request_payload':{**payload,'jsonExtParam':json.dumps({**extra,
                    'smsCode':code if code==FLOW_PROBE_CODE else '<provided>'},separators=(',',':'))},
                'path':path,'transport_profile':getattr(client.client,'net_age_transport_profile',None)}}
        submitted = False

        def before_submit():
            nonlocal submitted
            if not feature_running():
                raise CocError('网龄已关闭，领取流程已停止', 'FEATURE_DISABLED', 423)
            diagnostic = getattr(client, 'last_wheel_diagnostic', None)
            if isinstance(diagnostic, dict) and diagnostic:
                record['wheel_diagnostic'] = diagnostic
            record['submitted_at'] = self.now().isoformat()
            self.mark(login_id, group, record)
            submitted = True

        pending_message = '领取结果待核对，请稍后刷新'
        claim_failure = ''
        try:
            receipt = client.post(path, payload, encryption='wheel' if wheel else 'aurora',
                                  mutation=True, before_submit=before_submit,
                                  wheel_environment=wheel_environment)
        except CocError as error:
            if not submitted:
                raise
            if group in ('flow','birthday') and _wrong_sms(error):
                self.mark(login_id, group, previous)
                raise
            birthday_failed = group == 'birthday' and error.code == CLAIM_FAILED
            ambiguous = error.code == CLAIM_FAILED and str(error).startswith('领取失败')
            duplicate = group in ('flow', 'member', 'birthday') and error.code == MEMBER_ALREADY_CLAIMED
            flow_busy = group == 'flow' and not error.uncertain and (
                '活动太火爆了' in error.internal_message or '活动太火爆了' in str(error))
            if group == 'flow' and error.code == CLAIM_FAILED and not (flow_busy or error.uncertain):
                details = _claim_error_details(error, (code, access, client.user.get('token'), client.user.get('userId')))
                self.mark(login_id, group, {**record, 'response_code': error.code, **details})
                return {'message': FLOW_FAILURE_NOTICE, 'confirmed': False, 'code': CLAIM_FAILED}
            if not (birthday_failed or error.uncertain or error.code == 'UPSTREAM_FORMAT' or ambiguous or duplicate or flow_busy):
                self.mark(login_id, group, previous)
                raise
            failed = {**record, 'response_code': error.code}
            if error.trace_id:
                failed['trace_id'] = error.trace_id
            if duplicate:
                pending_message = '官方提示请勿重复领取，领取记录待核对'
            elif flow_busy:
                failed['response_busy'] = True
                pending_message = f'{FLOW_BUSY_NOTICE}。领取结果待核对'
            elif birthday_failed or (ambiguous and group == 'member'):
                details = _claim_error_details(error, (code, access, client.user.get('token'), client.user.get('userId')))
                failed.update(details)
                claim_failure = BIRTHDAY_FAILURE_NOTICE if birthday_failed else ENCRYPTION_FAILED
                if detail := details.get('response_detail'):
                    claim_failure += f' · 官方说明 {detail}'
                pending_message = f'{claim_failure}，订单暂未核对'
            self.mark(login_id, group, failed)
            if birthday_failed:
                if error.trace_id:
                    claim_failure += f' · 追踪号 {error.trace_id}'
                return {'message': claim_failure, 'confirmed': False, 'code': CLAIM_FAILED}
        else:
            if group == 'member' and isinstance(receipt, dict) and _received(({**receipt, 'activity_id': MEMBER_ID},), offer):
                self.mark(login_id, group, {**record, 'accepted': True})
                pending_message = '官方已受理会员领取，订单记录待同步'
        finally:
            if group in ('flow', 'member', 'birthday'):
                self.save_crypto_exchange(login_id, client)
        pending_result = {'message': pending_message, 'confirmed': False}
        try:
            history_ids = MEMBER_HISTORY_IDS if group == 'member' else BIRTH_IDS if group == 'birthday' else (offer['activity'],)
            orders = client.orders(list(history_ids), recover=False)
        except CocError:
            return pending_result
        period_orders = [item for item in orders if str(item.get('createTime', '')).startswith(period)]
        if _received(period_orders, offer):
            with self.lock:
                available = self.availability.get(login_id, {})
                if group in available.get('groups', ()):
                    self._save_availability(login_id, {**available, 'checked_at': self.now().isoformat(),
                        'groups': [remaining for remaining in available['groups'] if remaining != group]})
                self.mark(login_id, group, None)
            message = '官方记录已确认领取'
            if offer['id'] == 'qq-music':
                try:
                    binding = client.post('activities/growth/account/query', {'brand': 'QQ音乐'}, recover=False)
                except CocError:
                    binding = None
                nickname = binding.get('nickname') if isinstance(binding, dict) else None
                if isinstance(nickname, str) and nickname:
                    nickname = nickname if len(nickname) <= 10 else nickname[:6]+'****'
                    message += f'。发放账号：{nickname}'
                else:
                    message += '。QQ 音乐账号绑定状态待核对，请在 QQ 音乐确认到账。'
            return {'message': message, 'confirmed': True}
        if claim_failure:
            order_note = '本月已有会员订单，请核对领取内容' if period_orders else '本月暂未查到会员领取订单'
            pending_result['message'] = f'{claim_failure}，{order_note}'
        return pending_result


def register_net_age(app, credentials, accounts, cache, storage, recover):
    service = NetAge(credentials, accounts, cache, storage, recover)
    app.extensions['net_age'] = service
    surprise_scheduler=SurpriseScheduler(service,accounts,storage)
    app.extensions['net_age_surprise_scheduler']=surprise_scheduler
    app.extensions['net_age_surprise_login_restored']=surprise_scheduler.login_restored

    @app.get('/net-age')
    def net_age_page():
        return render_template('net_age.html')

    @app.get('/api/net-age/accounts')
    def net_age_accounts():
        rows = service.accounts()
        return jsonify(accounts=rows, available_counts=service.available_counts({account['id'] for account in rows
                       if account['status'] == 'active'}))

    @app.get('/api/net-age/availability')
    def net_age_availability():
        login_id = request.args.get('id')
        if not any(account['id'] == login_id and account['status'] == 'active' for account in service.accounts()):
            return jsonify(error='请选择已登录的共享账号。'), 401
        try:
            return jsonify(count=service.check_availability(login_id))
        except CocError as error:
            return jsonify(error=str(error), code=error.code), error.status

    @app.get('/api/net-age/status')
    def net_age_status():
        login_id = request.args.get('id')
        if not login_id:
            return jsonify(error='请选择共享账号。'), 400
        try:
            with service.connected(login_id) as client:
                return jsonify(service.status(login_id, client))
        except CocError as error:
            return jsonify(error=str(error), code=error.code), error.status

    @app.get('/api/net-age/surprise')
    def net_age_surprise():
        login_id = request.args.get('id')
        if not login_id:
            return jsonify(error='请选择共享账号。'), 400
        try:
            return jsonify(service.surprise_details(login_id))
        except CocError as error:
            return jsonify(error=str(error), code=error.code), error.status

    @app.get('/api/net-age/surprise/auto')
    def net_age_surprise_auto_status():
        return jsonify(surprise_scheduler.status())

    @app.post('/api/net-age/surprise/auto')
    def net_age_surprise_auto_run():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or not isinstance(payload.get('id'),str) or not payload['id']:
            return jsonify(error='请选择账号。'),400
        try:
            return jsonify(surprise_scheduler.run(payload['id']))
        except CocError as error:
            return jsonify(error=str(error),code=error.code),error.status

    @app.get('/api/net-age/diagnostic')
    def net_age_diagnostic():
        login_id = request.args.get('id')
        return jsonify(service.diagnostics.get(login_id) or {})

    @app.post('/api/net-age/claim')
    def net_age_claim():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('group') not in ('flow', 'member', 'ai', 'birthday') or not all(
                isinstance(payload.get(key), str) and payload[key] for key in ('id', 'choice')):
            return jsonify(error='请选择账号和权益。'), 400
        retry = payload.get('retry', False)
        if type(retry) is not bool or retry and payload['group'] not in RETRY_GROUPS:
            return jsonify(error='请选择可重新准备的权益。'), 400
        probe = payload.get('probe', False)
        if type(probe) is not bool or probe and payload['group'] not in ('flow','member','birthday'):
            return jsonify(error='请选择支持的权益验证模式。'), 400
        try:
            return jsonify(service.begin(payload['id'], payload['group'], payload['choice'], retry=retry, probe=probe))
        except CocError as error:
            service.record_diagnostic(payload['id'], 'prepare', error)
            return jsonify(error=str(error), code=error.code), error.status

    @app.post('/api/net-age/verification/<action>')
    def net_age_verification(action):
        payload = request.get_json(silent=True)
        if (action not in ('challenge', 'slider', 'auto-slider', 'sms', 'submit', 'cancel', 'probe') or not isinstance(payload, dict)
                or not all(isinstance(payload.get(key), str) and payload[key] for key in ('id', 'operation'))):
            return jsonify(error='请选择正在领取的权益。'), 400
        if action == 'slider' and (type(payload.get('position')) is not int or not 0 <= payload['position'] <= 360):
            return jsonify(error='请完成官方滑块验证。'), 400
        if action == 'submit' and not re.fullmatch(r'[0-9]{6}', str(payload.get('code', ''))):
            return jsonify(error='请输入 6 位短信验证码。'), 400
        try:
            return jsonify(service.verification(payload['id'], payload['operation'], action, payload))
        except CocError as error:
            service.record_diagnostic(payload['id'], action, error)
            with service.account_lock(payload['id']):
                operation=service.pending.get(payload['id'])
                active=operation is not None and operation.id==payload['operation']
                if active and error.status==401:
                    service.close_verification(payload['id'])
                    active=False
            return jsonify(error=str(error),code=error.code,trace_id=error.trace_id,
                           operation_active=active),error.status
