"""Pure Python implementation of the net-age Wheel protocol.

The public SDK field schema is a compatibility profile, not an executable
browser. Account data and cookies always come from the active COC session;
request time, key, nonce and verification are generated locally in Python.
"""
from copy import deepcopy
import base64
import json
from pathlib import Path
import struct
import time

from .coc_wheel import GDT_ACTIVITY, SevenDayCryptoError, prepare


PROFILE = json.loads(Path(__file__).with_name('net_age_profile.json').read_text(encoding='utf-8'))
MEMBER_PROFILE = json.loads(Path(__file__).with_name('net_age_member_profile.json').read_text(encoding='utf-8'))
# Same official page/SDK, with the birthday cinema confirmation call site.
# Columns are from chunk-a3acce52.e54386e0.js (UTF-16 source positions).
BIRTHDAY_PROFILE = deepcopy(MEMBER_PROFILE)
BIRTHDAY_PROFILE['last_call_stack']['claim'] = [frame.replace('s.PrizeDrawRight', 's.closeShareBirthdayNote2')
    .replace('.e54386e0.js:1:279599)', '.e54386e0.js:1:324789)')
    for frame in MEMBER_PROFILE['last_call_stack']['claim']]
MOBILE_H5_UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36')
MOBILE_H5_HEADERS = {
    'User-Agent':MOBILE_H5_UA,
    'Accept':'application/json, text/plain, */*',
    'Accept-Language':'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding':'gzip, deflate, br, zstd',
    'Priority':'u=1, i',
    'Sec-CH-UA':'"Not;A=Brand";v="8", "Chromium";v="150", "Android WebView";v="150"',
    'Sec-CH-UA-Mobile':'?1', 'Sec-CH-UA-Platform':'"Android"',
    'Sec-Fetch-Dest':'empty', 'Sec-Fetch-Mode':'cors', 'Sec-Fetch-Site':'same-origin',
    'X-Requested-With':'mark.via',
}


MEMBER_H5_HEADERS = {**MOBILE_H5_HEADERS,
    'User-Agent':('Mozilla/5.0 (Linux; Android 16; 2211133C Build/BP2A.250605.031.A3) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.7499.192 Mobile Safari/537.36'),
    'Sec-CH-UA':'"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
}


class PythonWheel:
    def __init__(self, profile=None, *, clock=None):
        self.profile = PROFILE if profile is None else profile
        self.clock = clock
        self.initialized = int(time.time()*1000)
        self.started = time.monotonic()
        self.context = None
        self.token = ''
        self.cookies = None
        self.events = []

    def prepare(self, cookies, user_agent, environment):
        source = environment() if callable(environment) else environment
        self.context = deepcopy(source)
        storage = source['local_storage']
        selected = {key:storage[key] for key in ('boundToken','pageId','channelId','userId') if key in storage}
        selected['benefit_phone'] = ''
        for key,value in storage.items():
            if key.endswith('_gdp_last_vst'):
                selected[key] = value
                break
        if storage.get('canvasworkbenchweb_auth'):
            auth = json.loads(storage['canvasworkbenchweb_auth'])
            phone = (auth.get('user_info') or {}).get('phone')
            if phone:
                selected['benefit_phone'] = phone
        self.context['local_storage'] = selected
        interaction = source.get('interaction')
        self.events = interaction['events'] if interaction else []
        if interaction:
            self.initialized = interaction['started_at']
        self.cookies = cookies if callable(cookies) else None
        return {'cookies':[], 'user_agent':user_agent}

    def _environment(self, phase, cookies, user_agent):
        now = self._now_ms()
        elapsed = int((time.monotonic()-self.started)*1000) if self.clock else now-self.initialized
        if self.cookies is not None:
            cookies = self.cookies()
        visible = [c for c in cookies if not c.get('httpOnly')]
        events, self.events = self.events, []
        return {**deepcopy(self.profile),
            'last_call_stack':self.profile['last_call_stack'][phase],
            'collection_time':now, 'init_time':now-elapsed,
            'time_elapsed':elapsed,
            'events':base64.b64encode(b''.join(struct.pack('<dBHHH', *event) for event in events)).decode(),
            'user_agent':user_agent, 'page_url':self.context['page_url'],
            'local_storage':deepcopy(self.context['local_storage']),
            'cookie':{c['name']:c['value'] for c in visible[:5]},
        }

    def _now_ms(self):
        return int(self.clock()) if self.clock else int(time.time()*1000)

    def encrypt(self, payload, cookies, user_agent, *, transport, environment=None):
        if self.context is None:
            raise SevenDayCryptoError('网龄验证会话尚未准备。', 'PYTHON_CONTEXT_MISSING')
        if not self.token:
            request = prepare({'activityId':GDT_ACTIVITY}, self._environment('gdt', cookies, user_agent), timestamp=self._now_ms())
            response = transport({'encryptedText':request['data']['cocEnContent']},
                {'Content-Type':'application/json', 'coc-aurora':request['headers']['coc-aurora'], 'Accept':'*/*'})
            if not isinstance(response, dict) or str(response.get('code')) != '0':
                raise SevenDayCryptoError('官方设备认证未通过，本次未提交领取。', 'GDT_REJECTED')
            token = response.get('data')
            if not isinstance(token, str) or not token:
                raise SevenDayCryptoError('官方未返回设备凭据，本次未提交领取。', 'GDT_TOKEN_MISSING')
            self.token = token
        result = prepare(payload, self._environment('claim', cookies, user_agent), self.token, [self.token], timestamp=self._now_ms())
        result['diagnostics'] = {'runtime':'python', 'context':{'wheel_reused':True,
            'member_page_checked':self.context.get('member_page_checked'),
            'clock_source':self.context.get('clock_source','local')}}
        return {**result, 'cookies':[], 'user_agent':user_agent}

    def close(self):
        self.context = None
        self.token = ''
        self.cookies = None
        self.events = []
