"""Account sessions with an in-process, target-only TCP connection path.

The loopback CONNECT listener forwards encrypted bytes unchanged. TLS, HTTP,
cookies and certificate verification remain owned by the Python HTTP client.
"""
import atexit
from email.utils import parsedate_to_datetime
import os
import select
import socket
import socketserver
import time
from threading import Lock, Thread
from urllib.request import Request

from http_transport import requests


TARGET = 'dev.coc.10086.cn'
BUSINESS_URLS = {
    f'https://{TARGET}/coc/wasmrc/gDT',
    f'https://{TARGET}/coc/activities/prize/templatePrize/new',
    f'https://{TARGET}/coc/user/auth/getSliderImg',
    f'https://{TARGET}/coc/user/auth/checkSliderImg',
    f'https://{TARGET}/coc/user/smsCode',
}
GDT_HEADER_ORDER = ('content-length,sec-ch-ua-platform,user-agent,sec-ch-ua,content-type,coc-aurora,'
    'sec-ch-ua-mobile,accept,origin,x-requested-with,sec-fetch-site,sec-fetch-mode,sec-fetch-dest,'
    'referer,accept-encoding,accept-language,cookie,priority')
CLAIM_HEADER_ORDER = ('content-length,sec-ch-ua-platform,sec-ch-ua,coc-aurora,sec-ch-ua-mobile,'
    'user-agent,accept,content-type,coc-en-version,origin,x-requested-with,sec-fetch-site,'
    'sec-fetch-mode,sec-fetch-dest,referer,accept-encoding,accept-language,cookie,priority')
_lock = Lock()
_relay = None


class _Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        try:
            self.request.settimeout(8)
            header = bytearray()
            while not header.endswith(b'\r\n\r\n') and len(header) < 4096:
                chunk = self.request.recv(1)
                if not chunk:
                    return
                header.extend(chunk)
            first = bytes(header).split(b'\r\n', 1)[0]
            if first not in (f'CONNECT {TARGET}:443 HTTP/1.1'.encode(),
                             f'CONNECT {TARGET}:443 HTTP/1.0'.encode()):
                self.request.sendall(b'HTTP/1.1 403 Target only\r\nConnection: close\r\n\r\n')
                return
            upstream = socket.create_connection((TARGET, 443), timeout=8)
            self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            self.request.settimeout(None)
            upstream.settimeout(None)
            while True:
                ready, _, _ = select.select((self.request, upstream), (), (), 1)
                for source in ready:
                    block = source.recv(65536)
                    if not block:
                        return
                    (upstream if source is self.request else self.request).sendall(block)
        except OSError:
            # The HTTP client's timeout/cancellation closes the encrypted tunnel.
            return
        finally:
            if upstream is not None:
                upstream.close()


class _Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True


def _close():
    global _relay
    with _lock:
        current, _relay = _relay, None
    if current is not None:
        server, thread = current
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _proxy():
    global _relay
    with _lock:
        if _relay is None:
            server = _Relay(('127.0.0.1', 0), _Tunnel)
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval':.2}, daemon=True)
            thread.start()
            _relay = server, thread
        return 'http://127.0.0.1:' + str(_relay[0].server_address[1])


atexit.register(_close)


class NetAgeSession(requests.Session):
    net_age_transport_profile = 'python-curl150-tcp-v2'
    def __init__(self, *, member=False):
        self.member = member
        self.net_age_user_id = ''
        self._clock_sample = None
        options={}
        if os.environ.get('LINGXI_ANDROID')!='1':
            from curl_cffi import CurlOpt
            options['curl_options']=({CurlOpt.COOKIELIST:'ALL', CurlOpt.STREAM_WEIGHT:220,
                CurlOpt.STREAM_EXCLUSIVE:1, CurlOpt.HTTP2_NO_PRIORITY:0} if member else
                {CurlOpt.HTTP2_NO_PRIORITY:0,CurlOpt.SPLIT_COOKIES:0,CurlOpt.COOKIELIST:'ALL'})
        if member:
            self.net_age_transport_profile = 'python-curl142-member-v2'
            super().__init__(impersonate='chrome142',default_headers=False,**options)
        else:
            super().__init__(impersonate='chrome150',default_headers=False,
                extra_fp={'http2_stream_weight':220,'http2_stream_exclusive':1},**options)

    def request(self, method, url, **kwargs):
        headers=({k.lower():v for k,v in {**dict(self.headers),**dict(kwargs.get('headers') or {})}.items()}
            if self.member else dict(kwargs.get('headers') or {}))
        if os.environ.get('LINGXI_ANDROID') != '1':
            cookie_request=Request(url)
            self.cookies.jar.add_cookie_header(cookie_request)
            cookie=cookie_request.get_header('Cookie')
            if cookie and not any(k.lower()=='cookie' for k in headers):
                headers['Cookie']=cookie
            kwargs['headers']=headers
        if self.member:
            if headers.get('sec-fetch-dest')=='document':
                headers.pop('origin',None)
            order=(GDT_HEADER_ORDER if url.endswith('/gDT') else CLAIM_HEADER_ORDER).split(',')
            headers={k.lower():v for k,v in headers.items()}
            kwargs['headers']={**{k:headers[k] for k in order if k in headers},
                **{k:v for k,v in headers.items() if k not in order}}
            if os.environ.get('LINGXI_ANDROID')!='1' and url.startswith('https://'+TARGET+'/'):
                kwargs['proxy']=_proxy()
                from curl_cffi import CurlOpt
                self.curl_options[CurlOpt.HTTPHEADER_ORDER]=','.join(order)
            original=self.headers
            self.headers={}
            try:
                started=time.monotonic()
                response=super().request(method,url,**kwargs)
                if url.endswith('/user/auth/checkSliderImg') and self.net_age_user_id:
                    self._observe_clock(response,started)
                return response
            finally:
                self.headers=original
        if os.environ.get('LINGXI_ANDROID') != '1' and method.upper() == 'POST' and url in BUSINESS_URLS:
            kwargs['proxy'] = _proxy()
            kwargs['extra_fp']={'http2_stream_weight':220,'http2_stream_exclusive':1,
                'header_order':GDT_HEADER_ORDER if url.endswith('/gDT') else CLAIM_HEADER_ORDER}
        return super().request(method, url, **kwargs)

    def _observe_clock(self, response, started):
        raw=next((value.split(';',1)[0].split('=',1)[1] for value in response.headers.get_list('set-cookie')
                  if value.startswith('RiskToken=')), '')
        # These fields are issued by the verified official HTTPS response.
        # Bind the sample to this account and corroborate its epoch with Date.
        if not (raw[:13].isdigit() and raw[45:-32]==self.net_age_user_id and response.headers.get('date')):
            return
        stamp=int(raw[:13])
        date=int(parsedate_to_datetime(response.headers['date']).timestamp()*1000)
        if 0<=stamp-date<2000:
            received=time.monotonic()
            self._clock_sample=(stamp+(received-started)*500, received)

    def business_time_ms(self):
        if self._clock_sample is None:
            return int(time.time()*1000)
        stamp,observed=self._clock_sample
        return int(stamp+(time.monotonic()-observed)*1000)
