"""Fixed-asset protocol runtime for the campus benefit risk and order flow."""
from __future__ import annotations

from base64 import b64decode
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from http_transport import RequestException, requests
from member_benefits_risk_runtime import run_dfp, run_wheel
from runtime_paths import DATA_DIR


ORIGIN = 'https://dev.coc.10086.cn'
PAGE_URL = (ORIGIN + '/coc3/canvas/rightsmarket-h5-canvas/online/zeromzone'
            '?channelCode=P00000011939&pageKey=3&pageRecorded=true')
GETTOKEN_URL = ORIGIN + '/coc3/gr/risk-ability/wasm/gettoken'
ORDER_URL = ORIGIN + '/coc3/coc3-market-order/api/external/order/placeOrder.do'
EDGE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0')
EDGE157_JA3 = ('771,4865-4866-4867-49195-49199-49196-49200-52393-52392-'
               '49171-49172-156-157-47-53,0-65281-11-65037-13-43-35-17613-'
               '51-18-23-10-16-27-5-45,4588-29-23-24,0')
EDGE157_AKAMAI = '1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p'
GLUE_SHA256 = '42446b9c1ef85df1094d0d06b8bae6da74f002f1407d5a8e783139e967add8a2'
WASM_SHA256 = '4f6f69cf9a46a8a2c3fb23ed4c31b08a9da5b2afc1bc2149ee7364c77f0570a8'
ROOT = Path(__file__).resolve().parent / 'recharge' / 'member_benefits_runtime'
DFP_STATE_SCOPE = 'member-campus-dfp-state'
WHEEL_MIN_LIFETIME_MS = 500


class CampusProtocolError(Exception):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class _RiskTransport:
    def __init__(self):
        self.session = requests.Session(
            impersonate='chrome150', ja3=EDGE157_JA3, akamai=EDGE157_AKAMAI)

    def __enter__(self):
        return self

    def request(self, payload):
        url = str(payload.get('url') or '')
        method = str(payload.get('method') or 'POST').upper()
        if method not in ('GET', 'POST') or url not in (GETTOKEN_URL, 'https://yxfk.market.chinamobile.com:8081/mgt/decision-engine/udid/c1'):
            raise ValueError('设备请求桥拒绝了非预期接口')
        headers = {str(key): str(value) for key, value in (payload.get('headers') or {}).items()
                   if str(key).lower() not in ('host', 'content-length', 'connection')}
        body = payload.get('body')
        kwargs = {'headers': headers, 'timeout': 20, 'allow_redirects': False}
        if body is not None:
            kwargs['data'] = str(body).encode()
        response = self.session.request(method, url, **kwargs)
        return {'status': response.status_code,
                'headers': {'content-type': response.headers.get('content-type', 'application/json'),
                            'etag': response.headers.get('etag', '')},
                'body': response.text,
                'transport': ('android-native' if os.environ.get('LINGXI_ANDROID') == '1'
                              else 'curl_cffi:chrome150-edge157-persistent')}

    def __exit__(self, *_args):
        self.session.close()


class CampusRiskProtocol:
    def __init__(self, storage=None, state_dir=None, *, transport_factory=_RiskTransport,
                 dfp_runtime=run_dfp, wheel_runtime=run_wheel):
        self.storage = storage
        self.state_dir = Path(state_dir or DATA_DIR / 'member-benefits')
        self.transport_factory = transport_factory
        self.dfp_runtime = dfp_runtime
        self.wheel_runtime = wheel_runtime

    def prepare(self, _cookies, token, _user_agent, entry, *, order=None, before_submit=None):
        if not isinstance(token, str) or len(token) != 36:
            raise CampusProtocolError('会员权益登录令牌格式无效。')
        page_url = PAGE_URL
        if entry:
            parsed = urlsplit(entry)
            allowed = (
                '/coc3/canvas/rightsmarket-h5-canvas/online/zeromzone',
                '/coc3/canvas/rightsmarket-h5-canvas/online/zero',
                '/coc3/canvas/rightsmarket-h5-canvas/online/halfprizemzone',
                '/coc3/canvas/rightsmarket-h5-canvas/online/halfprize',
            )
            if parsed.scheme != 'https' or parsed.netloc != 'dev.coc.10086.cn' or parsed.path not in allowed:
                raise CampusProtocolError('会员权益风控页面地址无效。')
            if parsed.path != allowed[0]:
                page_url = entry
        self.state_dir.mkdir(parents=True, exist_ok=True)
        account_key = sha256(token.encode()).hexdigest()[:24]
        saved_states = (self.storage.load_activity_data(DFP_STATE_SCOPE) or {}) if self.storage else {}
        legacy = self.state_dir / f'dfp-{account_key}.json'
        if self.storage and account_key not in saved_states and legacy.is_file():
            try:
                legacy_state = json.loads(legacy.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                legacy_state = None
            if isinstance(legacy_state, dict):
                saved_states = {**saved_states, account_key: legacy_state}
                self.storage.save_activity_data(DFP_STATE_SCOPE, saved_states)
                legacy.unlink()
        with self.transport_factory() as transport, TemporaryDirectory(dir=self.state_dir) as temporary:
            state = Path(temporary) / 'dfp-state.json'
            if isinstance(saved_states.get(account_key), dict):
                state.write_text(json.dumps(saved_states[account_key], ensure_ascii=False), encoding='utf-8')
            try:
                dfp = self.dfp_runtime(user_agent=EDGE_UA, page_url=page_url, platform='Win32',
                                       state_path=state, http_bridge=transport.request)
            except Exception as error:
                raise CampusProtocolError('官方设备令牌计算未完成。') from error
            current_state = None
            if state.is_file():
                try:
                    current_state = json.loads(state.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
            if self.storage and isinstance(current_state, dict):
                self.storage.save_activity_data(DFP_STATE_SCOPE, {**saved_states, account_key: current_state})
            details = dfp.get('result') or {}
            p3 = details.get('gr-risk-p3')
            if dfp.get('schema_version') != 1 or dfp.get('ok') is not True or not isinstance(p3, str) or len(p3) != 40:
                raise CampusProtocolError('官方服务风控没有返回有效设备令牌。')
            full_lengths = [int(item.get('param_length') or 0) for item in dfp.get('requests') or []
                            if int(item.get('param_length') or 0) > 500]
            if full_lengths and not all(1500 <= length <= 1800 for length in full_lengths):
                raise CampusProtocolError('官方服务风控参数长度异常。')
            try:
                wheel = self.wheel_runtime(token, EDGE_UA, page_url, platform='Win32', gr_risk_p3=p3,
                                           dfp_cache=str((current_state or {}).get('cache') or ''),
                                           wheel_min_lifetime_ms=WHEEL_MIN_LIFETIME_MS,
                                           http_bridge=transport.request)
            except Exception as error:
                raise CampusProtocolError('官方 Wheel 风控参数生成未完成。') from error

            values = (wheel.get('roto_simple') or {}).get('value')
            calls = wheel.get('calls') or []
            call = calls[0] if calls else {}
            assets = wheel.get('assets') or {}
            config = wheel.get('config') or {}
            context = wheel.get('context') or {}
            decoded = []
            if isinstance(values, list) and len(values) == 2:
                try:
                    decoded = [len(b64decode(str(value), validate=True)) for value in values]
                except ValueError:
                    decoded = []
            if (wheel.get('schema_version') != 1 or wheel.get('error') or
                    not isinstance(values, list) or len(values) != 2 or
                    len(str(values[0])) != 344 or not 600 <= len(str(values[1])) <= 900 or
                    decoded[:1] != [256] or len(decoded) != 2 or not 480 <= decoded[1] <= 532 or
                    not 8000 <= int(call.get('bodyLen') or 0) <= 12000 or
                    call.get('status') != 200 or str(call.get('responseCode')) != '0' or
                    call.get('responseSuccess') is not True or int(call.get('responseDataLength') or 0) != 32 or
                    not (str(call.get('transport') or '').startswith('curl_cffi:') or
                         call.get('transport') == 'android-native') or
                    assets.get('glue_sha256') != GLUE_SHA256 or assets.get('wasm_sha256') != WASM_SHA256 or
                    wheel.get('module_url_kind') != 'embedded-v8' or
                    config.get('user_agent') != EDGE_UA or config.get('page_url') != page_url or
                    int(config.get('wheel_min_lifetime_ms') or 0) < WHEEL_MIN_LIFETIME_MS or
                    int(context.get('local_storage_keys') or 0) < 15 or
                    int(context.get('session_storage_keys') or 0) < 7):
                raise CampusProtocolError('官方 Wheel 风控参数未通过本地完整性检查。')
            values = [str(value) for value in values]

            result = {'risk_token': p3, 'wasm_params': values, 'cookies': [], 'device': ''}
            if order is None:
                return result
            payload = {**order, 'operDfp': None}
            headers = {'phone': token, 'Referer': page_url,
                       'wasmParams': json.dumps(values, ensure_ascii=False, separators=(',', ':')),
                       'Accept': 'application/json, text/plain, */*',
                       'Content-Type': 'application/json;charset=UTF-8'}
            if before_submit is not None:
                before_submit()
            try:
                response = transport.session.post(ORDER_URL, headers=headers, json=payload,
                                                  timeout=30, allow_redirects=False)
            except RequestException as error:
                raise CampusProtocolError('未收到官方领取回执，请刷新核对领取状态。', uncertain=True) from error
            body = None
            if 200 <= response.status_code < 300 and response.content:
                try:
                    body = response.json()
                except ValueError as error:
                    raise CampusProtocolError('官方领取响应无法确认，请刷新核对领取状态。', uncertain=True) from error
                if not isinstance(body, dict):
                    raise CampusProtocolError('官方领取响应无法确认，请刷新核对领取状态。', uncertain=True)
            result['order'] = {'status': response.status_code, 'body': body}
            return result
