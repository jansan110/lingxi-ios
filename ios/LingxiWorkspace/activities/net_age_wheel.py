"""Node-hosted official Wheel runtime for the net-age monthly flow."""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
import time

from .coc_wheel import SevenDayCryptoError


SCRIPT = Path(__file__).with_suffix('.mjs')

# Match the device model declared by this workbench's AppLoginSession.envelope;
# the connected phone's different model belongs to the APP comparison only.
MOBILE_APP_UA = (
    'Mozilla/5.0 (Linux; Android 16; 2210132C; wv) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 '
    'Mobile Safari/537.36 leadeon/12.5.6/CMCCIT'
)
MOBILE_APP_HEADERS = {
    'User-Agent': MOBILE_APP_UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Sec-CH-UA': '"Chromium";v="143", "Not A(Brand";v="24", "Android WebView";v="143"',
    'Sec-CH-UA-Mobile': '?1',
    'Sec-CH-UA-Platform': '"Android"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'X-Requested-With': 'com.greenpoint.android.mc10086.activity',
}


class NodeWheel:
    """One local page lifecycle, bound to an in-progress account verification."""
    def __init__(self, *, max_old_space_mb=None):
        node = shutil.which('node')
        if node is None or not SCRIPT.is_file():
            raise SevenDayCryptoError('本机缺少 Node.js 网龄安全组件。', 'NODE_RUNTIME_MISSING')
        try:
            arguments = [node, '--experimental-vm-modules']
            if max_old_space_mb is not None:
                arguments.append(f'--max-old-space-size={max_old_space_mb}')
            self.process = subprocess.Popen(
                [*arguments, str(SCRIPT)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
            )
        except OSError as error:
            raise SevenDayCryptoError('官方网龄安全组件无法启动。', 'NODE_RUNTIME_FAILED') from error
        self.lines = Queue()
        self.reader = Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _request(self, command, transport=None):
        deadline = time.monotonic() + 55
        try:
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            while True:
                line = self.lines.get(timeout=max(.001, deadline-time.monotonic()))
                if line is None:
                    raise SevenDayCryptoError('官方网龄安全组件提前退出。', 'NODE_RUNTIME_FAILED')
                reply = json.loads(line)
                if reply.get('type') == 'http':
                    if transport is None:
                        raise SevenDayCryptoError('页面准备阶段发出了设备认证请求，本次已停止。', 'NODE_PREPARE_NETWORK')
                    value = transport(reply['body'], reply['headers'])
                    self.process.stdin.write(json.dumps({'type':'http_result', 'value':value}, ensure_ascii=False) + '\n')
                    self.process.stdin.flush()
                    continue
                if reply.get('ok') is not True:
                    raise SevenDayCryptoError(str(reply.get('error') or '官方网龄安全组件初始化未完成。'),
                                              str(reply.get('code') or 'NODE_RUNTIME_FAILED'))
                return reply['result']
        except Empty as error:
            raise SevenDayCryptoError('官方网龄安全组件响应超时。', 'NODE_RUNTIME_FAILED') from error
        except (ValueError, KeyError) as error:
            raise SevenDayCryptoError('官方网龄安全组件返回格式异常。', 'NODE_RUNTIME_FORMAT') from error

    def prepare(self, cookies, user_agent, environment):
        source = environment() if callable(environment) else environment
        return self._request({'type':'prepare', 'cookies':cookies, 'userAgent':user_agent,
            'storage':source.get('local_storage', {}), 'sessionStorage':source.get('session_storage', {}),
            'sourceLocations':bool(source.get('source_locations')),
            'vueRuntime':bool(source.get('vue_runtime')),
            'responseUrls':bool(source.get('response_urls')),
            'submitInteraction':source.get('submit_interaction'),
            'deviceProfile':source.get('device_profile'),
            'flowSlideProvinces':source.get('flow_slide_provinces', ''), 'probe':bool(source.get('probe'))})

    def encrypt(self, payload, cookies, user_agent, *, transport, environment=None):
        return self._request({'type':'encrypt', 'payload':payload}, transport)

    def record_interaction(self, value):
        return self._request({'type':'interaction', 'value':value})

    def close(self):
        self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()


def encrypt(payload, cookies, user_agent, *, transport=None, environment=None):
    if transport is None:
        raise SevenDayCryptoError('缺少网龄当前账号的设备认证会话。', 'NODE_TRANSPORT_MISSING')
    runtime = NodeWheel()
    try:
        runtime.prepare(cookies, user_agent, environment)
        return runtime.encrypt(payload, cookies, user_agent, transport=transport, environment=environment)
    finally:
        runtime.close()
