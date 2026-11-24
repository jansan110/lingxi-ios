"""Build Wheel environment records without exporting private argv/env."""
from pathlib import Path
import os
import platform
import sys
import time
import traceback

STARTED = time.monotonic()
INITIALIZED = int(time.time()*1000)


def collect():
    machine = platform.machine().lower()
    architecture = {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64'}.get(machine, machine)
    return {
        'runtime_type': 'Unknown non-browser environment', 'python_version': platform.python_version(),
        'platform': sys.platform, 'arch': architecture,
        'pid': float(os.getpid()), 'ppid': float(os.getppid()),
        'exec_path': Path(sys.executable).name,
        'argv': [Path(sys.argv[0]).name] if sys.argv else [],
        'environment_variables': {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ},
        'debugger_detected': int(sys.gettrace() is not None),
        'init_time': INITIALIZED,
        'time_elapsed': int((time.monotonic()-STARTED)*1000),
        'uptime_seconds': round(time.monotonic()-STARTED, 6),
        'last_call_stack': [Path(frame.filename).name+':'+str(frame.lineno)+':'+frame.name
                            for frame in traceback.extract_stack(limit=8)],
    }


def collect_mobile(user_agent):
    """Return the browser field schema collected by the official Wheel SDK."""
    return {
        'runtime_type': 'browser',
        'collection_time': int(time.time()*1000),
        'cookie_enabled': True,
        'debugger_detected': -1,
        'device_pixel_ratio': 2,
        'devtools_open': False,
        'events': '',
        'init_stack': [],
        'init_time': INITIALIZED,
        'language': 'zh-CN',
        'languages': ['zh-CN'],
        'last_call_stack': [],
        'max_touch_points': 1,
        'memory_js_heap_size_limit': 0,
        'memory_total_js_heap_size': 0,
        'memory_used_js_heap_size': 0,
        'mime_types': [],
        'on_line': True,
        'platform': 'Linux armv8l',
        'platform_touch': 'Linux armv8l:1',
        'plugins': [],
        'plugins_count': 0,
        'screen_color_depth': 24,
        'screen_height': 844,
        'screen_orientation_angle': 0,
        'screen_orientation_type': 'portrait-primary',
        'screen_pixel_depth': 24,
        'screen_width': 390,
        'time_elapsed': int((time.monotonic()-STARTED)*1000),
        'timezone_offset': -480,
        'touch_support': True,
        'user_agent': user_agent,
        'window_height': 844,
        'window_width': 390,
    }
