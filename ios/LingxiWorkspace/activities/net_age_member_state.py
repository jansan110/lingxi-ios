"""Account-local Python implementation of the page's GIO 3.8.3 storage.

Only analytics state lives here. Login cookies, RiskToken and one-use slider
credentials remain owned by the account's authenticated HTTP session.
"""
import base64
from copy import deepcopy
import json
import re
import time
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PREFIX = '9e4e5fa7244c6b6e_gdp_'
VERSION = 'gio-3.8.3-python-v1'
SESSION_SECONDS = 1800
VISITOR_SECONDS = 3650 * 86400


def encode(value):
    return 'gioenc-' + ''.join(char if char in 'AaZz@{' else chr(ord(char) ^ 1) for char in value)


class MemberPageState:
    def __init__(self, saved_cookies, masked_mobile):
        now = int(time.time())
        visitor = next((cookie for cookie in saved_cookies if cookie['name'] == 'gdp_user_id'), {})
        saved = visitor.get('net_age_sdk')
        self.state = deepcopy(saved) if saved and saved.get('version') == VERSION else None
        if self.state is None:
            # Migrate a genuine SDK visitor; the old random comma string is
            # not an encoded UUID and must not be used as a new SDK identity.
            value = unquote(visitor.get('value', ''))
            decoded = ''.join(c if c in 'AaZz@{' else chr(ord(c) ^ 1) for c in value[7:]) if value.startswith('gioenc-') else ''
            valid_uuid = bool(re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', decoded))
            device = str(UUID(decoded)) if valid_uuid else str(uuid4())
            self.state = {'version': VERSION, 'device': device,
                'visitor_expires': visitor.get('expires') if valid_uuid and visitor.get('expires') else now + VISITOR_SECONDS,
                'sequence': {'globalKey': 1, 'VISIT': 1, 'PAGE': 1}}
        padder = padding.PKCS7(128).padder()
        raw = padder.update(masked_mobile.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(b'9e5702ead4d643fd'), modes.ECB()).encryptor()
        alias = base64.b64encode(cipher.update(raw) + cipher.finalize()).decode()
        state = self.state
        new_session = not state.get('session') or state.get('session_expires', 0) <= now or state.get('alias') != alias
        if new_session:
            state['session'] = str(uuid4())
        state['session_expires'] = now + SESSION_SECONDS
        common = {'appVersion': '1.0.0', 'dataSourceId': 'aa410d5cd21666f5',
            'deviceId': state['device'], 'domain': 'dev.coc.10086.cn', 'language': 'zh-CN',
            'path': '/coc/web6/ageFeedbackV2/', 'platform': 'web',
            'query': 'pageId=1879720710683729920&channelId=P00000001090&iteration=true',
            'screenHeight': 873, 'screenWidth': 393, 'sdkVersion': '3.8.3',
            'sessionId': state['session'], 'timestamp': int(time.time() * 1000), 'title': '网龄成长计划'}
        sequence = state['sequence']
        if new_session:
            sequence['globalKey'] += 1
            sequence['VISIT'] += 1
        sequence['globalKey'] += 1
        sequence['PAGE'] += 1
        if new_session:
            visit = {'eventType': 'VISIT', **common, 'gioId': alias, 'userId': alias,
                'globalSequenceId': sequence['globalKey'], 'eventSequenceId': sequence['VISIT'], 'trackingId': 'g0'}
            state['last_visit'] = json.dumps({'value': encode(json.dumps(visit, ensure_ascii=False, separators=(',', ':'))),
                'expiredAt': 253402272000000}, separators=(',', ':'))
            sequence['globalKey'] += 1
            sequence['VISIT'] += 1
        state['alias'] = alias

    @property
    def local_storage(self):
        return {self.state['session'] + '_gdp_last_vst': self.state['last_visit']}

    def cookies(self):
        state = self.state
        values = [(PREFIX + 'user_key', ''), (PREFIX + 'session_id', state['session']),
            ('gdp_user_id', encode(state['device'])),
            (PREFIX + 'sequence_ids', json.dumps(state['sequence'], separators=(',', ':'))),
            (PREFIX + 'session_id_' + state['session'], 'true'),
            (PREFIX + 'cs1', encode(state['alias'])), (PREFIX + 'gio_id', encode(state['alias']))]
        result = [{'name': name, 'value': quote(value, safe="~!*'()-._"), 'domain': '.10086.cn', 'path': '/',
            'secure': False, 'httpOnly': False, 'hostOnly': False,
            'expires': state['session_expires'] if name == PREFIX + 'session_id' else state['visitor_expires']}
            for name, value in values]
        next(cookie for cookie in result if cookie['name'] == 'gdp_user_id')['net_age_sdk'] = deepcopy(state)
        return result
