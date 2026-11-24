"""Run the pinned DFP SDK and wheel WASM from the Python business flow.

Desktop uses the ``mini-racer`` Python package. Android delegates only the V8
execution boundary to the bundled native runtime and keeps state, transport,
validation and order control in Python. No Node.js process or WebView is used.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

ANDROID = os.environ.get("LINGXI_ANDROID") == "1"
if not ANDROID:
    from py_mini_racer import MiniRacer


HERE = Path(__file__).resolve().parent
RESOURCE_ROOT = HERE / "recharge" / "member_benefits_runtime"
WHEEL_DIR = RESOURCE_ROOT / "assets" / "wheel-0.0.8"
WHEEL_GLUE = WHEEL_DIR / "wheel-0.0.8.js"
WHEEL_WASM = WHEEL_DIR / "wheel_bg-0.0.8.wasm"
WHEEL_RUNTIME = RESOURCE_ROOT / "wheel_runtime.js"
WHEEL_GLUE_SHA256 = "42446b9c1ef85df1094d0d06b8bae6da74f002f1407d5a8e783139e967add8a2"
WHEEL_WASM_SHA256 = "4f6f69cf9a46a8a2c3fb23ed4c31b08a9da5b2afc1bc2149ee7364c77f0570a8"
DFP_SDK = RESOURCE_ROOT / "assets" / "const-id" / "const-id-1.1511.0.js"
DFP_RUNTIME = RESOURCE_ROOT / "dfp_runtime.js"
DFP_SDK_VERSION = "1.1511.0"
DFP_SDK_SHA256 = "f267b0f6a46b2b093204762b58aa321d7a25bfc0c150dfa42a657b53b76c96bc"
DFP_DEFAULT_SERVER = "https://yxfk.market.chinamobile.com:8081/mgt/decision-engine/udid/c1"
DFP_DEFAULT_APP_ID = "50c71040541846d2e70e393e73271c7f"
DFP_SCHEMA_VERSION = 1

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 "
    "Safari/537.36 Edg/157.0.0.0"
)
DEFAULT_PAGE_URL = (
    "https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/online/zeromzone"
    "?channelCode=P00000011939&pageKey=3&pageRecorded=true"
)

# Generated once from the verified 280x60 canvas output. The wheel always draws
# the same input, so preserving that PNG keeps the embedded runtime deterministic.
CANVAS_DATA_URL = "data:image/png;base64,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"

HttpBridge = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@lru_cache(maxsize=None)
def _verified_bytes(path: Path, expected_sha256: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"缺少固定资源: {path}") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(f"{path.name} SHA-256 不匹配: {actual}")
    return data


@lru_cache(maxsize=1)
def _dfp_sdk_source() -> str:
    try:
        text = DFP_SDK.read_bytes().decode("utf-8")
    except OSError as exc:
        raise RuntimeError(f"缺少固定资源: {DFP_SDK}") from exc
    marker = f"/*! const-id.js v{DFP_SDK_VERSION}"
    marker_at = text.find(marker)
    if marker_at < 0:
        raise RuntimeError("固定 DFP SDK 版本标记不匹配")
    source = text[marker_at:]
    actual = hashlib.sha256(source.encode()).hexdigest()
    if actual != DFP_SDK_SHA256:
        raise RuntimeError(f"{DFP_SDK.name} SHA-256 不匹配: {actual}")
    return source


@lru_cache(maxsize=None)
def _runtime_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"缺少运行时资源: {path}") from exc


@lru_cache(maxsize=2)
def _wheel_glue_source(trace_env: bool = False) -> str:
    source = _verified_bytes(WHEEL_GLUE, WHEEL_GLUE_SHA256).decode("utf-8")
    source = source.replace("export class Wheel", "class Wheel", 1)
    source = source.replace("export function main()", "function main()", 1)
    source = source.replace(
        "module_or_path = new URL('wheel_bg.wasm', import.meta.url);",
        "module_or_path = globalThis.__wheel_wasm_bytes;",
        1,
    )
    if trace_env:
        source = source.replace(
            "const ret = eval(getStringFromWasm0(arg0, arg1));",
            "const source = getStringFromWasm0(arg0, arg1); "
            "globalThis.__wheelEvalTrace?.(source); const ret = eval(source);",
            1,
        )
    source = source.replace(
        "export { initSync, __wbg_init as default };",
        "globalThis.__wheel_module = { Wheel, main, initSync, default: __wbg_init };",
        1,
    )
    if "export " in source or "import.meta" in source:
        raise RuntimeError("wheel glue 导出结构已变化")
    return source + "\n//# sourceURL=https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/WASM/wheel-0.0.8.js\n"


def _android_wheel(config: dict[str, Any], http_bridge: HttpBridge | None) -> dict[str, Any]:
    from java import jclass

    result = jclass("cn.pppyl.lingxiworkspace.MemberBenefitsV8").runWheel(
        json.dumps(config, ensure_ascii=False, separators=(",", ":")),
        _runtime_source(WHEEL_RUNTIME),
        _wheel_glue_source(bool(config.get("trace_env"))),
        base64.b64encode(_verified_bytes(WHEEL_WASM, WHEEL_WASM_SHA256)).decode("ascii"),
        http_bridge,
        60_000,
    )
    return json.loads(str(result))


def _android_dfp(
    config: dict[str, Any], cache: str, cache_reusable: bool,
    mock_server: bool, http_bridge: HttpBridge | None,
) -> dict[str, Any]:
    from java import jclass

    env = {
        "COC_USER_AGENT": config["user_agent"],
        "COC_PAGE_URL": config["page_url"],
        "COC_NAVIGATOR_PLATFORM": config["platform"],
        "COC_WHEEL_MIN_LIFETIME_MS": "0",
        "COC_COOKIE": _default_document_cookie(cache),
    }
    wheel_config = {
        "argv": [], "env": env,
        "host_platform": "win32" if config["platform"] == "Win32" else "linux",
        "hardware_concurrency": os.cpu_count() or 4,
        "random_seed": secrets.randbits(32),
        "random_pool": base64.b64encode(secrets.token_bytes(65536)).decode("ascii"),
        "canvas_data_url": CANVAS_DATA_URL,
        "dfp_cache": cache, "edge_context": None, "trace_env": False,
    }
    js_config = {
        "userAgent": config["user_agent"], "pageUrl": config["page_url"],
        "platform": config["platform"], "timeoutMs": config["timeout_ms"],
        "appId": config["app_id"], "server": config["server"],
        "mockServer": mock_server, "debug": False,
    }
    result = jclass("cn.pppyl.lingxiworkspace.MemberBenefitsV8").runDfp(
        json.dumps(wheel_config, ensure_ascii=False, separators=(",", ":")),
        _runtime_source(WHEEL_RUNTIME), _runtime_source(DFP_RUNTIME), _dfp_sdk_source(),
        json.dumps(js_config, ensure_ascii=False, separators=(",", ":")),
        json.dumps({"cache": cache, "reusable": cache_reusable}, separators=(",", ":")),
        http_bridge,
        max(15_000, config["timeout_ms"] * 4 + 5_000),
    )
    return json.loads(str(result))


def _default_document_cookie(dfp_cache: str = "") -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"

    def token(length: int) -> str:
        return "".join(secrets.choice(alphabet) for _ in range(length))

    visitor = ",".join(
        (f"{token(6)}-{token(8)}", token(4), token(4), token(4), token(12))
    )
    session_id = str(__import__("uuid").uuid4())
    sequence = "%7B%22globalKey%22%3A149%2C%22VISIT%22%3A4%2C%22PAGE%22%3A10%2C%22CUSTOM%22%3A137%7D"
    values = [f"gdp_user_id={visitor.replace(',', '%2C')}"]
    if dfp_cache:
        values.append(f"_zw_kvani5r={dfp_cache}")
    project = "9e4e5fa7244c6b6e"
    values.extend(
        (
            f"{project}_gdp_session_id={session_id}",
            f"{project}_gdp_session_id_sent={session_id}",
            f"{project}_gdp_sequence_ids={sequence}",
        )
    )
    return "; ".join(values)


async def _run_wheel_async(config: dict[str, Any], http_bridge: HttpBridge | None) -> dict[str, Any]:
    context = MiniRacer()
    context.set_hard_memory_limit(256 * 1024 * 1024)
    wasm = _verified_bytes(WHEEL_WASM, WHEEL_WASM_SHA256)
    runtime = _runtime_source(WHEEL_RUNTIME)

    async def bridge(payload_json: Any) -> str:
        if http_bridge is None:
            return json.dumps({"error": "未配置 Python HTTP 传输"}, ensure_ascii=False)
        payload = json.loads(str(payload_json))
        result = http_bridge(payload)
        if asyncio.iscoroutine(result):
            result = await result
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        context.eval(
            "globalThis.__PY_CONFIG = "
            + json.dumps(config, ensure_ascii=False, separators=(",", ":"))
            + ";"
        )
        async with context.wrap_py_function(bridge) as js_bridge:
            install_bridge = context.eval("(fn) => { globalThis.__py_http_json = fn; }")
            install_bridge(js_bridge)
            context.eval(runtime + "\n//# sourceURL=python-wheel-runtime.js\n")
            context.eval(_wheel_glue_source(bool(config.get("trace_env"))))
            encoded = base64.b64encode(wasm).decode("ascii")
            context.eval(
                "globalThis.__wheel_wasm_bytes = Uint8Array.from(atob("
                + json.dumps(encoded)
                + "), c => c.charCodeAt(0));"
            )
            promise = context.eval("__wheel_run()")
            try:
                raw = await asyncio.wait_for(promise, timeout=60)
            except TimeoutError as exc:
                stage = context.eval("globalThis.__wheel_stage || 'unknown'")
                calls = context.eval("JSON.stringify(typeof calls === 'undefined' ? [] : calls)")
                reads = context.eval("JSON.stringify(typeof ENV_READS === 'undefined' ? [] : ENV_READS.slice(-20))")
                raise RuntimeError(
                    f"wheel 运行超时，阶段: {stage}，calls={calls}，reads={reads}"
                ) from exc
        result = json.loads(str(raw))
        if result.get("error"):
            raise RuntimeError(f"wheel 运行失败: {result['error']}")
        return result
    finally:
        context.close()


def run_wheel(
    token: str,
    user_agent: str = DEFAULT_UA,
    page_url: str = DEFAULT_PAGE_URL,
    *,
    platform: str | None = None,
    gr_risk_p3: str = "",
    dfp_cache: str = "",
    phone: str = "",
    refresh_token: str = "",
    login_timestamp_ms: int | None = None,
    document_cookie: str = "",
    edge_context: dict[str, Any] | None = None,
    mock_gettoken: bool = False,
    emit_values: bool = True,
    trace_env: bool = False,
    wheel_min_lifetime_ms: int = 100,
    http_bridge: HttpBridge | None = None,
) -> dict[str, Any]:
    """Generate wheel/gettoken values entirely inside the Python process."""
    if not isinstance(token, str):
        raise TypeError("token 必须是字符串")
    if gr_risk_p3 and len(gr_risk_p3) != 40:
        raise ValueError("gr-risk-p3 必须是 40 个字符")
    if not 0 <= wheel_min_lifetime_ms <= 60_000:
        raise ValueError("wheel_min_lifetime_ms 必须在 0..60000 范围")
    parsed = urlparse(page_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("page_url 不是有效 HTTP(S) URL")

    mobile = any(marker in user_agent.lower() for marker in ("iphone", "ipad", "android"))
    host_platform = "win32" if (os.name == "nt" or platform == "Win32") else "linux"
    cache = dfp_cache if len(dfp_cache) in (45, 72) else ""
    cookie = document_cookie or _default_document_cookie(cache)
    env = {
        "COC_USER_AGENT": user_agent,
        "COC_PAGE_URL": page_url,
        "COC_NAVIGATOR_PLATFORM": platform or ("Win32" if os.name == "nt" else "Linux x86_64"),
        "COC_MOCK_GETTOKEN": "1" if mock_gettoken else "0",
        "COC_EMIT_VALUES": "1" if emit_values else "0",
        "COC_WHEEL_MIN_LIFETIME_MS": str(wheel_min_lifetime_ms),
        "COC_COOKIE": cookie,
        "PHONE_TOKEN": token,
        "COC_PHONE": phone,
        "COC_REFRESH_TOKEN": refresh_token,
        "COC_LOGIN_TIMESTAMP_MS": str(login_timestamp_ms or __import__("time").time_ns() // 1_000_000),
        "COC_GR_RISK_P3": gr_risk_p3,
    }
    config = {
        "argv": (["--mock-gettoken"] if mock_gettoken else [])
        + (["--emit-values"] if emit_values else [])
        + (["--trace-env"] if trace_env else []),
        "env": env,
        "host_platform": host_platform,
        "hardware_concurrency": os.cpu_count() or 4,
        "random_seed": secrets.randbits(32),
        "random_pool": base64.b64encode(secrets.token_bytes(65536)).decode("ascii"),
        "canvas_data_url": CANVAS_DATA_URL,
        "dfp_cache": cache,
        "edge_context": edge_context,
        "trace_env": trace_env,
        "mobile": mobile,
    }
    if ANDROID:
        return _android_wheel(config, http_bridge)
    return asyncio.run(_run_wheel_async(config, http_bridge))


def _dfp_fingerprint(user_agent: str, page_url: str, platform: str) -> str:
    value = json.dumps(
        {
            "sdk": DFP_SDK_SHA256,
            "userAgent": user_agent,
            "pageUrl": page_url,
            "platform": platform,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _load_dfp_state(path: Path, fingerprint: str) -> tuple[str, bool, str]:
    if not path.exists():
        return "", False, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", False, "unreadable"
    if data.get("schema_version") != DFP_SCHEMA_VERSION:
        return "", False, "schema"
    if data.get("sdk_sha256") != DFP_SDK_SHA256:
        return "", False, "sdk"
    if data.get("config_fingerprint") != fingerprint:
        return "", False, "config"
    cache = data.get("cache")
    if not isinstance(cache, str) or len(cache) not in (45, 72):
        return "", False, "invalid"
    if not all(0x21 <= ord(char) <= 0x7E for char in cache):
        return "", False, "invalid"
    return cache, True, "ok"


def _save_dfp_state(path: Path, fingerprint: str, cache: str) -> bool:
    if len(cache) not in (45, 72):
        return False
    payload = {
        "schema_version": DFP_SCHEMA_VERSION,
        "sdk_version": DFP_SDK_VERSION,
        "sdk_sha256": DFP_SDK_SHA256,
        "config_fingerprint": fingerprint,
        "cache": cache,
        "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(5)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return True


def _dfp_mock_response(payload: dict[str, Any], cache_reusable: bool) -> dict[str, Any]:
    headers = {str(k).lower(): str(v) for k, v in (payload.get("headers") or {}).items()}
    param = headers.get("param", "")
    token = "6af70000" + "m" * 32
    if len(param) > 500:
        body = {"status": 2, "data": token, "msg": "mock token generated"}
    elif len(param) == 204 and cache_reusable:
        body = {"status": 1, "data": token, "msg": "mock token valid"}
    else:
        body = {"status": -6, "msg": "mock full fingerprint required"}
    response_headers = {"content-type": "application/json"}
    if body["status"] in (1, 2):
        response_headers["etag"] = token
    return {
        "status": 200,
        "headers": response_headers,
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        "transport": "mock",
    }


async def _run_dfp_async(
    config: dict[str, Any],
    cache: str,
    cache_reusable: bool,
    mock_server: bool,
    http_bridge: HttpBridge | None,
) -> dict[str, Any]:
    context = MiniRacer()
    context.set_hard_memory_limit(256 * 1024 * 1024)
    runtime = _runtime_source(WHEEL_RUNTIME)
    dfp_runtime = _runtime_source(DFP_RUNTIME)
    sdk = _dfp_sdk_source()

    async def bridge(payload_json: Any) -> str:
        payload = json.loads(str(payload_json))
        if mock_server:
            result = _dfp_mock_response(payload, cache_reusable)
        elif http_bridge is not None:
            result = http_bridge(payload)
            if asyncio.iscoroutine(result):
                result = await result
        else:
            try:
                from curl_cffi import requests as curl_requests

                response = await asyncio.to_thread(
                    curl_requests.request,
                    payload.get("method") or "GET",
                    payload["url"],
                    headers=payload.get("headers") or {},
                    data=payload.get("body"),
                    timeout=config["timeout_ms"] / 1000,
                    impersonate="edge",
                )
                result = {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text,
                    "transport": "curl-cffi-edge",
                }
            except Exception as exc:  # bridge boundary: return a JS-visible error
                result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    env = {
        "COC_USER_AGENT": config["user_agent"],
        "COC_PAGE_URL": config["page_url"],
        "COC_NAVIGATOR_PLATFORM": config["platform"],
        "COC_WHEEL_MIN_LIFETIME_MS": "0",
        "COC_COOKIE": _default_document_cookie(cache),
    }
    wheel_config = {
        "argv": [],
        "env": env,
        "host_platform": "win32" if os.name == "nt" else "linux",
        "hardware_concurrency": os.cpu_count() or 4,
        "random_seed": secrets.randbits(32),
        "random_pool": base64.b64encode(secrets.token_bytes(65536)).decode("ascii"),
        "canvas_data_url": CANVAS_DATA_URL,
        "dfp_cache": cache,
        "edge_context": None,
        "trace_env": False,
    }
    try:
        context.eval(
            "globalThis.__PY_CONFIG = "
            + json.dumps(wheel_config, ensure_ascii=False, separators=(",", ":"))
            + ";"
        )
        async with context.wrap_py_function(bridge) as js_bridge:
            context.eval("(fn) => { globalThis.__py_http_json = fn; }")(js_bridge)
            context.eval(runtime + "\n//# sourceURL=python-dfp-environment.js\n")
            context.eval(dfp_runtime + "\n//# sourceURL=python-dfp-runtime.js\n")
            js_config = {
                "userAgent": config["user_agent"],
                "pageUrl": config["page_url"],
                "platform": config["platform"],
                "timeoutMs": config["timeout_ms"],
                "appId": config["app_id"],
                "server": config["server"],
                "mockServer": mock_server,
                "debug": False,
            }
            context.eval(
                "globalThis.__DFP_SDK_SOURCE = "
                + json.dumps(sdk, ensure_ascii=False)
                + ";"
            )
            expression = (
                "__dfp_runtime_run(__DFP_SDK_SOURCE, "
                + json.dumps(js_config, ensure_ascii=False, separators=(",", ":"))
                + ", "
                + json.dumps({"cache": cache, "reusable": cache_reusable}, separators=(",", ":"))
                + ")"
            )
            raw = await asyncio.wait_for(context.eval(expression), timeout=max(15, config["timeout_ms"] / 250))
        return json.loads(str(raw))
    finally:
        context.close()


def run_dfp(
    *,
    user_agent: str = DEFAULT_UA,
    page_url: str = DEFAULT_PAGE_URL,
    platform: str | None = None,
    state_path: Path | None = None,
    app_id: str = DFP_DEFAULT_APP_ID,
    server: str = DFP_DEFAULT_SERVER,
    timeout_ms: int = 2000,
    mock_server: bool = False,
    emit_token: bool = True,
    http_bridge: HttpBridge | None = None,
) -> dict[str, Any]:
    """Generate ``gr-risk-p3`` using the pinned SDK in embedded V8."""
    if not 100 <= timeout_ms <= 60_000:
        raise ValueError("timeout_ms 必须在 100..60000 范围")
    platform = platform or ("Win32" if os.name == "nt" else "Linux x86_64")
    state_path = state_path or (HERE / ".cache" / ("dfp-state.mock.json" if mock_server else "dfp-state.json"))
    fingerprint = _dfp_fingerprint(user_agent, page_url, platform)
    cache, reusable, reason = _load_dfp_state(state_path, fingerprint)
    config = {
        "user_agent": user_agent,
        "page_url": page_url,
        "platform": platform,
        "timeout_ms": timeout_ms,
        "app_id": app_id,
        "server": server,
    }
    started = time.perf_counter()
    result = (_android_dfp(config, cache, reusable, mock_server, http_bridge) if ANDROID else
              asyncio.run(_run_dfp_async(config, cache, reusable, mock_server, http_bridge)))
    token = str(result.get("token") or "")
    if len(token) != 40:
        raise RuntimeError(f"DFP 未返回 40 字符 gr-risk-p3（长度 {len(token)}）")
    new_cache = str(result.get("cache") or "")
    persisted = _save_dfp_state(state_path, fingerprint, new_cache)
    output: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "mock": mock_server,
        "sdk": {"version": DFP_SDK_VERSION, "sha256": DFP_SDK_SHA256, "source": "bundled"},
        "config": {
            "user_agent": user_agent,
            "page_url": page_url,
            "platform": platform,
            "app_id_length": len(app_id),
        },
        "state": {
            "path": str(state_path),
            "cache_reused": reusable,
            "previous_reason": reason,
            "cache_length": len(new_cache),
            "cache_sha256": hashlib.sha256(new_cache.encode()).hexdigest(),
            "fast_path_eligible": len(new_cache) == 72,
            "persisted": persisted,
        },
        "requests": result.get("records") or [],
        "result": {
            "token_length": len(token),
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
    }
    if emit_token:
        output["result"]["gr-risk-p3"] = token
    return output
