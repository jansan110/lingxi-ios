"""Pure Python Wheel 0.0.8 envelopes for the net-age surprise lottery.

Pinned WASM SHA-256: 4f6f69cf9a46a8a2c3fb23ed4c31b08a9da5b2afc1bc2149ee7364c77f0570a8.
Offline SDK vectors establish the RSA/AES frame and verification algorithm.
The public environment profile describes the pinned SDK's compatibility shape;
it contains no account state or captured authentication values.
"""
from __future__ import annotations

import base64
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .coc_wheel import _json, verification


GETTOKEN_URL = 'https://dev.coc.10086.cn/coc3/gr/risk-ability/wasm/gettoken'
GDT_ACTIVITY = '109230918290'
PUBLIC_KEY = b'''-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApBesMcH39Cnm+k0LWwxj
lCU10LE/fvExJVJblDAVkH4zmzGi2WXkvNz7AO7ihBomdlncDXYT37k/1EMetK0k
ULkb+LhGcX5oyFZrBJdJSWVTnX5j7CtJ+B5zDXcL9Hsy5CWYpNhNm6up813H6TTm
SRJ9KMMwf78W+dAPtYFzh54B0qVZ6iPWO/xuYYM6GGpnzKGqVhSwytBw7ICaOyxP
C91BtBmbrbhXgefsyfuQaTN07POmDbgXl2iXei3aY7WFI+QzCDN0gLwKCRxW2huC
tihTi+rO6jJ+/R/NlpJjeyEk1EjiN/RyxtZwyafDHcvYdDrsuvIoxsEHdxfTr2qA
owIDAQAB
-----END PUBLIC KEY-----
'''
_public_key = serialization.load_pem_public_key(PUBLIC_KEY)
_profile = json.loads(Path(__file__).with_name('net_age_surprise_profile.json').read_text(encoding='utf-8'))


class SurpriseProtocolError(Exception):
    def __init__(self, message, code='SURPRISE_PROTOCOL', status=502):
        super().__init__(message)
        self.code, self.status = code, status


def encrypt_frame(payload, environment, timestamp, *, key=None, nonce=None):
    """Return the official [RSA-wrapped AES key, nonce/ciphertext/tag] pair."""
    frame = {'data': payload, 'environment': environment, 'timestamp': timestamp,
             'verification': verification(environment, timestamp)}
    key = secrets.token_bytes(32) if key is None else key
    nonce = secrets.token_bytes(12) if nonce is None else nonce
    content = nonce + AESGCM(key).encrypt(nonce, _json(frame), None)
    return [base64.b64encode(_public_key.encrypt(key, padding.PKCS1v15())).decode(),
            base64.b64encode(content).decode()]


@dataclass
class _DeviceState:
    token: str = ''
    history: list = field(default_factory=list)
    pending: Future | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


_states = {}
_states_lock = threading.RLock()


class SurpriseWheel:
    """Keep one device context per login token, page and user agent.

    ``clock`` returns Unix seconds. ``storage`` is the application's encrypted
    load_activity_data/save_activity_data service. ``transport(body, headers)``
    sends one gettoken request through the authenticated Python HTTP session.
    A device token has no SDK-provided expiry; it remains reusable until an
    explicit ``invalidate`` after an official rejection. Business submission
    and any decision to recover its authentication remain with the caller.
    """
    def __init__(self, token, user_agent, page_url, *, clock=None, storage=None, cookies=()):
        parsed = urlsplit(page_url)
        if (not isinstance(token, str) or not token.strip() or not isinstance(user_agent, str)
                or not user_agent.strip() or parsed.scheme != 'https' or parsed.netloc != 'dev.coc.10086.cn'):
            raise SurpriseProtocolError('网龄惊喜盲盒的账号或页面上下文无效。', 'SURPRISE_CONTEXT', 400)
        self.token, self.user_agent, self.page_url = token, user_agent, page_url
        self.clock, self.storage = clock or time.time, storage
        self.started = int(self.clock() * 1000)
        self.location = parsed
        if hasattr(cookies, 'items'):
            self.cookies = dict(cookies.items())
        else:
            self.cookies = {item['name']: item['value'] for item in cookies
                            if not item.get('httpOnly') and not item.get('httponly') and not item.get('http_only')}
        agent = user_agent.lower()
        self.platform = ('Linux armv8l' if 'android' in agent else 'iPhone' if 'iphone' in agent
                         else 'iPad' if 'ipad' in agent else 'MacIntel' if 'macintosh' in agent
                         else 'Win32' if 'windows' in agent else 'Linux x86_64')
        self.mobile = any(marker in agent for marker in ('android', 'iphone', 'ipad'))
        binding = hashlib.sha256(_json([token, user_agent, page_url])).hexdigest()
        self.scope = 'net-age-surprise-wheel-' + binding
        with _states_lock:
            cache_key = (id(storage), self.scope)
            state = _states.get(cache_key)
            if state is None:
                saved = (storage.load_activity_data(self.scope) or {}) if storage else {}
                state = _DeviceState(saved.get('device_token', ''), list(saved.get('history', [])))
                _states[cache_key] = state
            self._state = state

    def _environment(self, stamp):
        environment = deepcopy(_profile)
        if self.mobile:
            environment.update(canvas_fingerprint='', webgl={}, plugins=[], plugins_count=0,
                               mime_types=[], max_touch_points=1, device_pixel_ratio=2,
                               memory_js_heap_size_limit=0, memory_total_js_heap_size=0,
                               memory_used_js_heap_size=0, screen_width=390, screen_height=844,
                               window_width=390, window_height=844, screen_orientation_type='portrait-primary')
        with self._state.lock:
            history = list(self._state.history)
        environment.update(
            collection_time=stamp, cookie=dict(self.cookies), device_token=history,
            hardware_concurrency=os.cpu_count() or 1, hostname=self.location.hostname,
            init_stack=[], init_time=self.started, last_call_stack=[],
            local_storage={'benefit_phone': self.token}, page_url=self.page_url,
            platform=self.platform, platform_touch=self.platform + ':' + str(environment['max_touch_points']),
            port=self.location.port and str(self.location.port) or '', protocol=self.location.scheme + ':',
            time_elapsed=stamp - self.started,
            timezone_offset=int((time.altzone if time.localtime(stamp / 1000).tm_isdst > 0 else time.timezone) / 60),
            tls_fingerprint={'has_subtle_crypto': False, 'is_secure_context': True,
                             'protocol': self.location.scheme + ':',
                             'user_agent_contains_ssl': 'ssl' in self.user_agent.lower()},
            user_agent=self.user_agent,
        )
        return environment

    def _device_token(self, transport):
        with self._state.lock:
            if self._state.token:
                return self._state.token
            owner = self._state.pending is None
            if owner:
                self._state.pending = Future()
            pending = self._state.pending
        if not owner:
            return pending.result()
        try:
            stamp = int(self.clock() * 1000)
            pair = encrypt_frame({'activityId': GDT_ACTIVITY, 'deviceToken': ''}, self._environment(stamp), stamp)
            response = transport({'benefit_phone': self.token, 'encryptedText': pair[1],
                                  'timestamp': int(self.clock() * 1000)},
                                 {'Content-Type': 'application/json', 'coc-aurora': pair[0]})
            if (not isinstance(response, dict) or str(response.get('code')) != '0'
                    or response.get('success') is False):
                detail = str(response.get('message') or '官方未返回原因')[:160] if isinstance(response, dict) else '响应格式异常'
                raise SurpriseProtocolError('官方设备认证未完成：' + detail + '。', 'GDT_REJECTED')
            token = response.get('data')
            if not isinstance(token, str) or not token.strip():
                raise SurpriseProtocolError('官方未返回有效设备凭据，本次没有提交抽奖。', 'GDT_TOKEN_MISSING')
            with self._state.lock:
                self._state.token = token
                self._state.history.append(token)
                if self.storage:
                    self.storage.save_activity_data(self.scope, {'device_token': token, 'history': list(self._state.history)})
                self._state.pending = None
                pending.set_result(token)
            return token
        except Exception as error:
            # Complete the one shared acquisition failure for every waiter.
            # Nothing is retried here, and no business request is sent here.
            with self._state.lock:
                self._state.pending = None
                pending.set_exception(error)
            raise

    def sign(self, payload=None, *, transport):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise SurpriseProtocolError('网龄惊喜盲盒请求格式无效。', 'SURPRISE_INPUT', 400)
        token = self._device_token(transport)
        stamp = int(self.clock() * 1000)
        environment = {'deviceToken': token, 'platform': self.platform,
                       'time_elapsed': stamp - self.started, 'url': self.page_url,
                       'user_agent': self.user_agent}
        return {'wasm_params': encrypt_frame(payload, environment, stamp), 'device_token': token}

    def invalidate(self):
        """Clear only this account/context after an explicit official rejection."""
        with self._state.lock:
            self._state.token = ''
            self._state.history.clear()
            if self.storage:
                self.storage.save_activity_data(self.scope, {'device_token': '', 'history': []})
