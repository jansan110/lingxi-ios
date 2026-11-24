// Python 进程内的 wheel 0.0.8 运行环境。由 py-mini-racer/V8 执行，无 Node.js 依赖。
const PY_CONFIG = globalThis.__PY_CONFIG || {};
const process = {
  argv: ["python", "wheel_runtime", ...(PY_CONFIG.argv || [])],
  env: PY_CONFIG.env || {},
  platform: PY_CONFIG.host_platform || "win32",
  pid: 0,
  stderr: { write() {} },
  stdout: { write() {} },
  exit() {},
};
const os = { cpus: () => Array.from({ length: PY_CONFIG.hardware_concurrency || 28 }, () => ({})) };
const path = {
  join: (...parts) => parts.filter(Boolean).join("/"),
  dirname: () => ".",
  resolve: (value) => String(value || "."),
};
const fileURLToPath = () => ".";
const pathToFileURL = (value) => ({ href: String(value) });

function encodeUtf8(value) {
  const text = unescape(encodeURIComponent(String(value)));
  return Uint8Array.from(text, (char) => char.charCodeAt(0));
}
function decodeUtf8(value) {
  const text = Array.from(new Uint8Array(value), (byte) => String.fromCharCode(byte)).join("");
  return decodeURIComponent(escape(text));
}
class TextEncoderPolyfill { encode(value) { return encodeUtf8(value); } }
class TextDecoderPolyfill { decode(value) { return decodeUtf8(value || new Uint8Array()); } }
globalThis.TextEncoder = globalThis.TextEncoder || TextEncoderPolyfill;
globalThis.TextDecoder = globalThis.TextDecoder || TextDecoderPolyfill;

const BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";
globalThis.btoa = globalThis.btoa || ((input) => {
  let output = "";
  const value = String(input);
  for (let block = 0, charCode, index = 0, map = BASE64_ALPHABET;
       value.charAt(index | 0) || (map = "=", index % 1);
       output += map.charAt(63 & block >> 8 - index % 1 * 8)) {
    charCode = value.charCodeAt(index += 3 / 4);
    if (charCode > 255) throw new TypeError("btoa input is not Latin-1");
    block = block << 8 | charCode;
  }
  return output;
});
globalThis.atob = globalThis.atob || ((input) => {
  const value = String(input).replace(/=+$/, "").replace(/\s+/g, "");
  let output = "";
  if (value.length % 4 === 1) throw new TypeError("invalid base64");
  for (let bc = 0, bs = 0, buffer, index = 0;
       (buffer = value.charAt(index++));
       ~buffer && (bs = bc % 4 ? bs * 64 + buffer : buffer, bc++ % 4)
         ? output += String.fromCharCode(255 & bs >> (-2 * bc & 6)) : 0) {
    buffer = BASE64_ALPHABET.indexOf(buffer);
  }
  return output;
});

class HeadersPolyfill {
  constructor(init = {}) {
    this.map = new Map();
    if (init instanceof HeadersPolyfill) {
      for (const [name, value] of init) this.set(name, value);
    } else if (Array.isArray(init)) {
      for (const pair of init) this.set(pair[0], pair[1]);
    } else {
      for (const [name, value] of Object.entries(init || {})) this.set(name, value);
    }
  }
  set(name, value) { this.map.set(String(name).toLowerCase(), String(value)); }
  append(name, value) {
    const key = String(name).toLowerCase();
    this.set(key, this.has(key) ? `${this.get(key)}, ${value}` : value);
  }
  get(name) { return this.map.get(String(name).toLowerCase()) ?? null; }
  has(name) { return this.map.has(String(name).toLowerCase()); }
  delete(name) { this.map.delete(String(name).toLowerCase()); }
  entries() { return this.map.entries(); }
  keys() { return this.map.keys(); }
  values() { return this.map.values(); }
  forEach(callback, thisArg) { for (const [k, v] of this.map) callback.call(thisArg, v, k, this); }
  [Symbol.iterator]() { return this.entries(); }
}
globalThis.Headers = HeadersPolyfill;

class URLPolyfill {
  constructor(value, base = undefined) {
    let source = String(value);
    if (!/^[a-z][a-z0-9+.-]*:/i.test(source) && base) {
      const root = new URLPolyfill(base);
      source = source.startsWith("/") ? root.origin + source : root.origin + root.pathname.replace(/[^/]*$/, "") + source;
    }
    const match = source.match(/^([a-z][a-z0-9+.-]*:)(?:\/\/([^/?#]*))?([^?#]*)(\?[^#]*)?(#.*)?$/i);
    if (!match) throw new TypeError(`Invalid URL: ${source}`);
    this.protocol = match[1];
    this.host = match[2] || "";
    const hostParts = this.host.split(":");
    this.hostname = hostParts[0] || "";
    this.port = hostParts.length > 1 ? hostParts.slice(1).join(":") : "";
    this.pathname = match[3] || "/";
    this.search = match[4] || "";
    this.hash = match[5] || "";
    this.origin = this.host ? `${this.protocol}//${this.host}` : "null";
  }
  get href() { return `${this.origin === "null" ? this.protocol : this.origin}${this.pathname}${this.search}${this.hash}`; }
  set href(value) { const next = new URLPolyfill(value); Object.assign(this, next); }
  toString() { return this.href; }
  toJSON() { return this.href; }
}
globalThis.URL = URLPolyfill;
class URLSearchParamsPolyfill {
  constructor(init = "") {
    this.items = [];
    if (typeof init === "string") {
      for (const part of init.replace(/^\?/, "").split("&")) {
        if (!part) continue;
        const at = part.indexOf("=");
        this.append(
          decodeURIComponent(at < 0 ? part : part.slice(0, at)),
          decodeURIComponent(at < 0 ? "" : part.slice(at + 1)),
        );
      }
    } else if (init && typeof init[Symbol.iterator] === "function") {
      for (const pair of init) this.append(pair[0], pair[1]);
    } else {
      for (const [name, value] of Object.entries(init || {})) this.append(name, value);
    }
  }
  append(name, value) { this.items.push([String(name), String(value)]); }
  set(name, value) { this.delete(name); this.append(name, value); }
  get(name) { return this.items.find((item) => item[0] === String(name))?.[1] ?? null; }
  has(name) { return this.items.some((item) => item[0] === String(name)); }
  delete(name) { const key = String(name); this.items = this.items.filter((item) => item[0] !== key); }
  entries() { return this.items[Symbol.iterator](); }
  [Symbol.iterator]() { return this.entries(); }
  toString() { return this.items.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&"); }
}
globalThis.URLSearchParams = URLSearchParamsPolyfill;

class RequestPolyfill {
  constructor(input, init = {}) {
    const previous = input instanceof RequestPolyfill ? input : null;
    this.url = previous ? previous.url : String(input);
    this.method = String(init.method || previous?.method || "GET").toUpperCase();
    this.headers = new HeadersPolyfill(init.headers || previous?.headers || {});
    this.body = init.body !== undefined ? init.body : previous?.body;
    this.signal = init.signal || previous?.signal || null;
  }
  clone() { return new RequestPolyfill(this); }
  async text() { return this.body == null ? "" : String(this.body); }
}
class ResponsePolyfill {
  constructor(body = "", init = {}) {
    this._body = body == null ? "" : body;
    this.status = Number(init.status || 200);
    this.statusText = init.statusText || "";
    this.headers = new HeadersPolyfill(init.headers || {});
    this.url = init.url || "";
    this.ok = this.status >= 200 && this.status < 300;
  }
  async text() { return typeof this._body === "string" ? this._body : decodeUtf8(this._body); }
  async json() { return JSON.parse(await this.text()); }
  async arrayBuffer() { return encodeUtf8(await this.text()).buffer; }
  clone() { return new ResponsePolyfill(this._body, { status: this.status, statusText: this.statusText, headers: this.headers, url: this.url }); }
}
globalThis.Request = RequestPolyfill;
globalThis.Response = ResponsePolyfill;
class AbortControllerPolyfill {
  constructor() { this.signal = { aborted: false }; }
  abort() { this.signal.aborted = true; }
}
globalThis.AbortController = globalThis.AbortController || AbortControllerPolyfill;

let randomState = (PY_CONFIG.random_seed || Date.now()) >>> 0;
const randomPool = PY_CONFIG.random_pool
  ? Uint8Array.from(atob(PY_CONFIG.random_pool), (char) => char.charCodeAt(0))
  : new Uint8Array();
let randomPoolOffset = 0;
function nextRandomByte() {
  if (randomPoolOffset < randomPool.length) return randomPool[randomPoolOffset++];
  randomState ^= randomState << 13; randomState ^= randomState >>> 17; randomState ^= randomState << 5;
  return randomState & 255;
}
globalThis.crypto = globalThis.crypto || {
  getRandomValues(target) { for (let i = 0; i < target.length; i += 1) target[i] = nextRandomByte(); return target; },
  randomUUID() { return randomUUID(); },
};
const randomBytes = (length) => crypto.getRandomValues(new Uint8Array(length));
const randomUUID = () => {
  const bytes = randomBytes(16);
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
const createHash = () => {
  let value = "";
  return {
    update(data) { value += typeof data === "string" ? data : String(data); return this; },
    digest() {
      if (value.startsWith("data:image/png;base64,")) return "ca7db1433e74c16f4d5e662cf8559bf40480ffef6988819e1702f3b4355ca7ee";
      let hash = 2166136261;
      for (let i = 0; i < value.length; i += 1) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619) >>> 0;
      return hash.toString(16).padStart(8, "0").repeat(8);
    },
  };
};
globalThis.performance = globalThis.performance || { now: () => Date.now() };
// mini-racer's zero-delay timer path can resolve synchronously and expose a
// non-Promise Atomics.waitAsync value. A 1 ms floor keeps browser semantics.
const nativeSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, delay = 0, ...args) =>
  nativeSetTimeout(() => callback(...args), Math.max(1, Number(delay) || 1));
globalThis.queueMicrotask = globalThis.queueMicrotask || ((callback) => Promise.resolve().then(callback));
globalThis.requestAnimationFrame = globalThis.requestAnimationFrame || ((callback) => setTimeout(() => callback(Date.now()), 16));
globalThis.cancelAnimationFrame = globalThis.cancelAnimationFrame || ((handle) => clearTimeout(handle));
if (typeof globalThis.setInterval !== "function") {
  let nextIntervalId = 1;
  const intervals = new Map();
  globalThis.setInterval = (callback, delay = 0, ...args) => {
    const id = nextIntervalId++;
    const tick = () => {
      if (!intervals.has(id)) return;
      callback(...args);
      intervals.set(id, setTimeout(tick, delay));
    };
    intervals.set(id, setTimeout(tick, delay));
    return id;
  };
  globalThis.clearInterval = (id) => {
    if (intervals.has(id)) clearTimeout(intervals.get(id));
    intervals.delete(id);
  };
}

function createCanvas(width, height) {
  const context = {
    fillStyle: "#000000", font: "10px sans-serif", textBaseline: "alphabetic",
    fillRect() {}, fillText() {}, measureText() { return { width: 10 }; },
    getImageData() { return { data: new Uint8ClampedArray(4) }; },
    createLinearGradient() { return { addColorStop() {} }; },
  };
  return {
    width, height,
    getContext(type) { return type === "2d" ? context : null; },
    toDataURL() { return PY_CONFIG.canvas_data_url || "data:image/png;base64,iVBORw0KGgo="; },
  };
}

async function hostFetch(input, init = {}) {
  if (typeof globalThis.__py_http_json !== "function") throw new Error("Python HTTP bridge is unavailable");
  const request = input instanceof RequestPolyfill ? input : new RequestPolyfill(input, init);
  const raw = await globalThis.__py_http_json(JSON.stringify({
    url: request.url, method: request.method,
    headers: Object.fromEntries(request.headers), body: request.body == null ? null : String(request.body),
  }));
  const result = JSON.parse(String(raw));
  if (result.error) throw new Error(result.error);
  const response = new ResponsePolyfill(result.body || "", {
    status: result.status || 200, headers: result.headers || {}, url: request.url,
  });
  response._transport = result.transport || "python-embedded";
  return response;
}
globalThis.fetch = hostFetch;

const fs = {
  existsSync() { return false; },
  readFileSync() { throw new Error("filesystem access is handled by Python"); },
  mkdirSync() {}, writeFileSync() {}, renameSync() {},
};

const out = (...a) => process.stderr.write(a.join(" ") + "\n");
console.log = out;

const argv = process.argv.slice(2);
const option = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
};
const flag = (name) => argv.includes(name);
const numberOption = (name, envName, fallback) => {
  const raw = option(name) ?? process.env[envName];
  const value = raw === undefined ? fallback : Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} 必须是数字`);
  return value;
};

const DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0";
const DEFAULT_PAGE_URL = "https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/online/zeromzone?channelCode=P00000011939&pageKey=3&pageRecorded=true";
const UA = option("--user-agent") || process.env.COC_USER_AGENT || DEFAULT_UA;
const PAGE_URL = option("--page-url") || process.env.COC_PAGE_URL || DEFAULT_PAGE_URL;
const pageLocation = new URL(PAGE_URL);
const MOBILE_UA = /iphone|ipad|android/i.test(UA);
const NAV_PLATFORM = option("--platform") || process.env.COC_NAVIGATOR_PLATFORM ||
  (process.platform === "win32" ? "Win32" : process.platform === "darwin" ? "MacIntel" : "Linux x86_64");
const NAV_VENDOR = option("--vendor") || process.env.COC_NAVIGATOR_VENDOR ||
  "Google Inc.";
const VIEWPORT_WIDTH = numberOption("--viewport-width", "COC_VIEWPORT_WIDTH", MOBILE_UA ? 390 : 1365);
const VIEWPORT_HEIGHT = numberOption("--viewport-height", "COC_VIEWPORT_HEIGHT", MOBILE_UA ? 844 : 768);
const INNER_WIDTH = numberOption("--inner-width", "COC_INNER_WIDTH", VIEWPORT_WIDTH);
const INNER_HEIGHT = numberOption("--inner-height", "COC_INNER_HEIGHT", VIEWPORT_HEIGHT);
const DEVICE_PIXEL_RATIO = numberOption("--device-pixel-ratio", "COC_DEVICE_PIXEL_RATIO", MOBILE_UA ? 2 : 1);
const HARDWARE_CONCURRENCY = numberOption("--hardware-concurrency", "COC_HARDWARE_CONCURRENCY", os.cpus().length || 4);
const DEVICE_MEMORY_RAW = option("--device-memory") ?? process.env.COC_DEVICE_MEMORY;
const DEVICE_MEMORY = DEVICE_MEMORY_RAW === undefined
  ? (MOBILE_UA ? undefined : 32)
  : numberOption("--device-memory", "COC_DEVICE_MEMORY", 32);
const MAX_TOUCH_POINTS = numberOption("--max-touch-points", "COC_MAX_TOUCH_POINTS", MOBILE_UA ? 1 : 10);
const CLIENT_HINT_UA = option("--sec-ch-ua") || process.env.COC_SEC_CH_UA ||
  '"Not-A.Brand";v="99", "Microsoft Edge";v="157", "Chromium";v="157"';
const ACCEPT_LANGUAGE = option("--accept-language") || process.env.COC_ACCEPT_LANGUAGE || "zh-CN";
const CLIENT_HINT_MOBILE = /iphone|ipad|android/i.test(UA) ? "?1" : "?0";
const CLIENT_HINT_PLATFORM = /iphone|ipad/i.test(UA) ? '"iOS"' : /android/i.test(UA) ? '"Android"' :
  /windows/i.test(UA) ? '"Windows"' : /macintosh|mac os/i.test(UA) ? '"macOS"' : '"Linux"';
const EMIT_VALUES = flag("--emit-values") || process.env.COC_EMIT_VALUES === "1";
const MOCK_GETTOKEN = flag("--mock-gettoken") || process.env.COC_MOCK_GETTOKEN === "1";
const TRACE_ENV = flag("--trace-env");
const ENV_READS = [];
const STACK_SUMMARIES = [];
let LAST_CANVAS_SUMMARY = null;
const traceEnv = (name, values = []) => {
  if (TRACE_ENV) ENV_READS.push(`${name}:${JSON.stringify(values)}`);
};
const NATIVE_FUNCTION_SOURCES = new WeakMap();
const originalFunctionToString = Function.prototype.toString;
const functionToStringDescriptor = Object.getOwnPropertyDescriptor(Function.prototype, "toString");
const functionToStringProxy = new Proxy(originalFunctionToString, {
  apply(target, thisArg, args) {
    return NATIVE_FUNCTION_SOURCES.get(thisArg) || Reflect.apply(target, thisArg, args);
  },
});
Object.defineProperty(Function.prototype, "toString", {
  ...functionToStringDescriptor,
  value: functionToStringProxy,
});
NATIVE_FUNCTION_SOURCES.set(
  functionToStringProxy,
  Reflect.apply(originalFunctionToString, originalFunctionToString, []),
);
const markNativeFunction = (fn, name) => {
  NATIVE_FUNCTION_SOURCES.set(fn, `function ${name}() { [native code] }`);
  return fn;
};
const GLUE_STACK_URL = "https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/WASM/wheel-0.0.8.js";
const APP_STACK_URL = "https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/rightsmarket-h5-canvas/build_production/js/app~d0ae3f07.6d8d6002.js";
Error.prepareStackTrace = (error, frames) => {
  const title = `${error.name || "Error"}${error.message ? `: ${error.message}` : ""}`;
  let stack = `${title}\n${frames.map((frame) => `    at ${frame}`).join("\n")}`;
  stack = stack
    .replace(/file:\/\/\/[^)\n]*wheel-0\.0\.8(?:\.trace)?\.mjs/g, GLUE_STACK_URL)
    .replace(/python-wheel-runtime\.js/g, APP_STACK_URL);
  STACK_SUMMARIES.push({ length: stack.length, sha256: sha256(stack) });
  traceEnv("error.stack", [stack]);
  return stack;
};
if (TRACE_ENV) {
  globalThis.__wheelEvalTrace = (source) => traceEnv("eval.source", [String(source)]);
  globalThis.__wheelToStringTrace = (target, result) => {
    traceEnv("function.toString", [
      typeof target, typeof target === "function" ? target.name : "",
      String(result).slice(0, 120),
    ]);
  };
}
const LEGACY_WARMUP = option("--wheel-warmup-ms") ?? process.env.COC_WHEEL_WARMUP_MS;
const WHEEL_MIN_LIFETIME_MS = LEGACY_WARMUP === undefined
  ? numberOption("--wheel-min-lifetime-ms", "COC_WHEEL_MIN_LIFETIME_MS", 500)
  : Number(LEGACY_WARMUP);
if (!Number.isFinite(WHEEL_MIN_LIFETIME_MS) || WHEEL_MIN_LIFETIME_MS < 0 || WHEEL_MIN_LIFETIME_MS > 60_000) {
  throw new Error("Wheel 最小生命周期必须在 0..60000ms 之间");
}
const EXPLICIT_ASSET_DIR = option("--asset-dir") || process.env.WHEEL_ASSET_DIR;
const HERE = ".";
const GETTOKEN_BRIDGE = process.env.COC_GETTOKEN_BRIDGE || path.join(HERE, "gettoken_transport.py");
const PYTHON_BRIDGE = process.env.COC_PYTHON_FETCH_BRIDGE || (process.platform === "win32" ? "python" : "python3");
const PERSISTENT_BRIDGE_URL = process.env.COC_FETCH_BRIDGE_URL || "";
const PERSISTENT_BRIDGE_TOKEN = process.env.COC_FETCH_BRIDGE_TOKEN || "";
const USE_NATIVE_FETCH = flag("--native-fetch") || process.env.COC_NATIVE_FETCH === "1";
function loadEdgeContext() {
  const parsed = PY_CONFIG.edge_context || null;
  if (!parsed) return null;
  if (parsed?.schema_version !== 1 || !parsed?.environment?.storage) {
    throw new Error("Edge context 文件格式不正确");
  }
  return { path: "python-config", data: parsed };
}
const EDGE_CONTEXT = loadEdgeContext();
function loadDfpCache() {
  const cache = typeof PY_CONFIG.dfp_cache === "string" ? PY_CONFIG.dfp_cache : "";
  return /^[\x21-\x7e]{45}$|^[\x21-\x7e]{72}$/.test(cache) ? cache : "";
}
const DFP_CACHE = loadDfpCache();
const randomBase36 = (length) => {
  const alphabet = "0123456789abcdefghijklmnopqrstuvwxyz";
  const bytes = randomBytes(length);
  return Array.from(bytes, (value) => alphabet[value % alphabet.length]).join("");
};
function createPageCookie(dfpCache) {
  const project = "9e4e5fa7244c6b6e";
  const visitor = [
    `${randomBase36(6)}-${randomBase36(8)}`,
    randomBase36(4), randomBase36(4), randomBase36(4), randomBase36(12),
  ].join(",");
  const sessionId = randomUUID();
  const sequence = JSON.stringify({ globalKey: 149, VISIT: 4, PAGE: 10, CUSTOM: 137 });
  const encodedSequence = sequence.replaceAll('"', "%22").replaceAll(",", "%2C");
  return [
    `gdp_user_id=${encodeURIComponent(visitor)}`,
    ...(dfpCache ? [`_zw_kvani5r=${dfpCache}`] : []),
    `${project}_gdp_session_id=${sessionId}`,
    `${project}_gdp_session_id_sent=${sessionId}`,
    `${project}_gdp_sequence_ids=${encodedSequence}`,
  ].join("; ");
}
const DOCUMENT_COOKIE = option("--cookie") || process.env.COC_COOKIE ||
  EDGE_CONTEXT?.data?.environment?.cookie || createPageCookie(DFP_CACHE);
const EDGE_HEADER_SOURCE = EDGE_CONTEXT?.data?.gettoken?.request?.headers_all || {};
const EDGE_HEADER_ALLOWLIST = new Set([
  "accept", "accept-language", "cookie", "origin", "priority", "referer",
  "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
  "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "user-agent",
]);
const EDGE_HEADERS = Object.fromEntries(Object.entries(EDGE_HEADER_SOURCE)
  .filter(([name]) => EDGE_HEADER_ALLOWLIST.has(String(name).toLowerCase())));
const BUNDLED_ASSET_DIR = path.join(HERE, "assets", "wheel-0.0.8");
const ASSET_CACHE = path.join(HERE, ".cache", "wheel-0.0.8");
const LEGACY_ASSET_DIR = process.env.TEMP ? path.join(process.env.TEMP, "zmz-wasm") : "/tmp/zmz-wasm";
const ASSETS = {
  glue: {
    name: "wheel-0.0.8.js",
    url: "https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/WASM/wheel-0.0.8.js",
    sha256: "42446b9c1ef85df1094d0d06b8bae6da74f002f1407d5a8e783139e967add8a2",
  },
  wasm: {
    name: "wheel_bg-0.0.8.wasm",
    url: "https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/WASM/wheel_bg-0.0.8.wasm",
    sha256: "4f6f69cf9a46a8a2c3fb23ed4c31b08a9da5b2afc1bc2149ee7364c77f0570a8",
  },
};
const calls = [];
const realFetch = globalThis.fetch.bind(globalThis);

const sha256 = (data) => createHash("sha256").update(data).digest("hex");

function validatedAsset(dir, spec) {
  const candidate = path.join(dir, spec.name);
  if (!fs.existsSync(candidate)) return null;
  const data = fs.readFileSync(candidate);
  return sha256(data) === spec.sha256 ? { path: candidate, data } : null;
}

async function downloadAsset(spec) {
  fs.mkdirSync(ASSET_CACHE, { recursive: true });
  const response = await realFetch(spec.url, { headers: { "user-agent": UA } });
  if (!response.ok) throw new Error(`下载 ${spec.name} 失败: HTTP ${response.status}`);
  const data = Buffer.from(await response.arrayBuffer());
  const actual = sha256(data);
  if (actual !== spec.sha256) {
    throw new Error(`${spec.name} SHA-256 不匹配: ${actual}`);
  }
  const destination = path.join(ASSET_CACHE, spec.name);
  const temporary = `${destination}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, data);
  fs.renameSync(temporary, destination);
  return { path: destination, data };
}

async function resolveAssets() {
  const searchDirs = EXPLICIT_ASSET_DIR
    ? [path.resolve(EXPLICIT_ASSET_DIR)]
    : [BUNDLED_ASSET_DIR, ASSET_CACHE, LEGACY_ASSET_DIR];
  for (const candidateDir of searchDirs) {
    const glue = validatedAsset(candidateDir, ASSETS.glue);
    const wasm = validatedAsset(candidateDir, ASSETS.wasm);
    if (glue && wasm) return { glue, wasm, source_dir: candidateDir };
  }
  if (EXPLICIT_ASSET_DIR || flag("--offline-assets")) {
    throw new Error("未找到同时通过 SHA-256 校验的 wheel 0.0.8 JS/WASM 资源");
  }
  return {
    glue: await downloadAsset(ASSETS.glue),
    wasm: await downloadAsset(ASSETS.wasm),
    source_dir: ASSET_CACHE,
  };
}

// wasm-bindgen uses concrete web_sys instanceof checks before collecting
// document/canvas/WebGL fields. Plain object stubs silently skip those paths.
class Window {}
Object.defineProperty(Window.prototype, "ontouchstart", {
  value: null, writable: true, configurable: true, enumerable: true,
});
class Document {}
class HTMLDocument extends Document {
  constructor() {
    super();
    this.cookie = DOCUMENT_COOKIE;
    this.referrer = "";
    this.title = "中国移动";
    this.frameElement = null;
    this.documentElement = { clientWidth: VIEWPORT_WIDTH, clientHeight: VIEWPORT_HEIGHT };
  }
  createElement(tag) {
    traceEnv("document.createElement", [String(tag)]);
    return String(tag).toLowerCase() === "canvas"
      ? new HTMLCanvasElement()
      : { style: {}, textContent: "", appendChild(){}, setAttribute(){}, getAttribute(){ return null; } };
  }
  querySelector() { return null; }
}
class CanvasRenderingContext2D {
  constructor(nativeContext) { this._native = nativeContext; }
  get fillStyle() { return this._native.fillStyle; }
  set fillStyle(value) {
    this._native.fillStyle = value;
    traceEnv("canvas.fillStyle", [String(value)]);
  }
  get font() { return this._native.font; }
  set font(value) { this._native.font = value; traceEnv("canvas.font", [String(value)]); }
  get textBaseline() { return this._native.textBaseline; }
  set textBaseline(value) {
    this._native.textBaseline = value;
    traceEnv("canvas.textBaseline", [String(value)]);
  }
  fillRect(...args) { traceEnv("canvas.fillRect", args); return this._native.fillRect(...args); }
  fillText(...args) { traceEnv("canvas.fillText", args); return this._native.fillText(...args); }
  measureText(...args) { return this._native.measureText(...args); }
  getImageData(...args) { return this._native.getImageData(...args); }
  createLinearGradient(...args) { return this._native.createLinearGradient(...args); }
}
class WebGLRenderingContext {
  getParameter(n) {
    traceEnv("webgl.getParameter", [n]);
    const strings = {
      0x1F00: "WebKit",
      0x1F01: "WebKit WebGL",
      0x1F02: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
      0x1F03: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
      0x9245: "Google Inc. (NVIDIA)",
      0x9246: "ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti (0x00002D04) Direct3D11 vs_5_0 ps_5_0, D3D11)",
    };
    if (n in strings) return strings[n];
    const numbers = {
      0x0D33: 16384, 0x84E8: 16384, 0x851C: 16384, 0x8869: 16, 0x8872: 16,
      0x8B4C: 16, 0x8B4D: 32, 0x8B4F: 4096, 0x8DFB: 4095,
      0x8DFC: 30, 0x8DFD: 1024,
    };
    const arrays = {
      0x0D3A: new Int32Array([32767, 32767]),
      0x846D: new Float32Array([1, 1024]),
      0x846E: new Float32Array([1, 1]),
    };
    if (n in arrays) return arrays[n];
    return n in numbers ? numbers[n] : null;
  }
  getExtension(name) {
    traceEnv("webgl.getExtension", [name]);
    if (name === "WEBGL_debug_renderer_info") {
      return { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
    }
    if (!this.getSupportedExtensions().includes(name)) return null;
    if (name === "EXT_texture_filter_anisotropic") {
      return { TEXTURE_MAX_ANISOTROPY_EXT: 0x84FE, MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84FF };
    }
    return {};
  }
  getSupportedExtensions() {
    traceEnv("webgl.getSupportedExtensions");
    return [
      "ANGLE_instanced_arrays","EXT_blend_minmax","EXT_clip_control","EXT_color_buffer_half_float",
      "EXT_depth_clamp","EXT_disjoint_timer_query","EXT_float_blend","EXT_frag_depth",
      "EXT_polygon_offset_clamp","EXT_shader_texture_lod","EXT_texture_compression_bptc",
      "EXT_texture_compression_rgtc","EXT_texture_filter_anisotropic","EXT_texture_mirror_clamp_to_edge",
      "EXT_sRGB","KHR_parallel_shader_compile","OES_element_index_uint","OES_fbo_render_mipmap","OES_standard_derivatives",
      "OES_texture_float","OES_texture_float_linear","OES_texture_half_float",
      "OES_texture_half_float_linear","OES_vertex_array_object","WEBGL_blend_func_extended","WEBGL_color_buffer_float",
      "WEBGL_compressed_texture_s3tc","WEBGL_compressed_texture_s3tc_srgb",
      "WEBGL_debug_renderer_info","WEBGL_debug_shaders","WEBGL_depth_texture",
      "WEBGL_draw_buffers","WEBGL_lose_context","WEBGL_multi_draw","WEBGL_polygon_mode",
    ];
  }
  getShaderPrecisionFormat(...args) {
    traceEnv("webgl.getShaderPrecisionFormat", args);
    const precisionType = args[1];
    return precisionType >= 0x8DF3
      ? { precision: 0, rangeMin: 31, rangeMax: 30 }
      : { precision: 23, rangeMin: 127, rangeMax: 127 };
  }
  getContextAttributes() { return { alpha: true, antialias: true, depth: true, stencil: false }; }
}
class HTMLCanvasElement {
  constructor() { this._native = createCanvas(300, 150); }
  get width() { return this._native.width; }
  set width(value) { this._native.width = value; traceEnv("canvas.width", [value]); }
  get height() { return this._native.height; }
  set height(value) { this._native.height = value; traceEnv("canvas.height", [value]); }
  getContext(type) {
    traceEnv("canvas.getContext", [type]);
    if (type === "2d") return new CanvasRenderingContext2D(this._native.getContext("2d"));
    if (type === "webgl" || type === "experimental-webgl") return new WebGLRenderingContext();
    return null;
  }
  toDataURL() {
    const value = this._native.toDataURL();
    LAST_CANVAS_SUMMARY = { length: value.length, sha256: sha256(value) };
    traceEnv("canvas.toDataURL", [value.length]);
    return value;
  }
}

const mimeTypeData = new WeakMap();
function MimeType(type, description, suffixes) {
  mimeTypeData.set(this, { type, description, suffixes, enabledPlugin: null });
}
delete MimeType.prototype.constructor;
for (const name of ["type", "suffixes", "description", "enabledPlugin"]) {
  Object.defineProperty(MimeType.prototype, name, {
    get() { return mimeTypeData.get(this)?.[name]; }, enumerable: true, configurable: true,
  });
}
Object.defineProperty(MimeType.prototype, "constructor", {
  value: MimeType, writable: true, configurable: true,
});
Object.defineProperty(MimeType.prototype, Symbol.toStringTag, { value: "MimeType" });

const pluginData = new WeakMap();
function Plugin(name, description, filename, mimeTypes) {
  pluginData.set(this, { name, description, filename, mimeTypes });
  mimeTypes.forEach((mime, index) => {
    Object.defineProperty(this, String(index), { value: mime, enumerable: true, configurable: true });
    Object.defineProperty(this, mime.type, { value: mime, enumerable: false, configurable: true });
  });
}
delete Plugin.prototype.constructor;
for (const name of ["name", "filename", "description"]) {
  Object.defineProperty(Plugin.prototype, name, {
    get() { return pluginData.get(this)?.[name]; }, enumerable: true, configurable: true,
  });
}
Object.defineProperty(Plugin.prototype, "length", {
  get() { return pluginData.get(this)?.mimeTypes.length ?? 0; }, enumerable: true, configurable: true,
});
Object.defineProperty(Plugin.prototype, "item", {
  value: function item(index) { return this[String(index)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(Plugin.prototype, "namedItem", {
  value: function namedItem(name) { return this[String(name)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(Plugin.prototype, "constructor", {
  value: Plugin, writable: true, configurable: true,
});
Object.defineProperty(Plugin.prototype, Symbol.toStringTag, { value: "Plugin" });
Plugin.prototype[Symbol.iterator] = Array.prototype[Symbol.iterator];

const mimeTypeArrayData = new WeakMap();
function MimeTypeArray(items) {
  mimeTypeArrayData.set(this, items);
  items.forEach((item, index) => {
    Object.defineProperty(this, String(index), { value: item, enumerable: true, configurable: true });
    Object.defineProperty(this, item.type, { value: item, enumerable: false, configurable: true });
  });
}
delete MimeTypeArray.prototype.constructor;
Object.defineProperty(MimeTypeArray.prototype, "length", {
  get() { return mimeTypeArrayData.get(this)?.length ?? 0; }, enumerable: true, configurable: true,
});
Object.defineProperty(MimeTypeArray.prototype, "item", {
  value: function item(index) { return this[String(index)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(MimeTypeArray.prototype, "namedItem", {
  value: function namedItem(name) { return this[String(name)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(MimeTypeArray.prototype, "constructor", {
  value: MimeTypeArray, writable: true, configurable: true,
});
Object.defineProperty(MimeTypeArray.prototype, Symbol.toStringTag, { value: "MimeTypeArray" });
MimeTypeArray.prototype[Symbol.iterator] = Array.prototype[Symbol.iterator];

const pluginArrayData = new WeakMap();
function PluginArray(items) {
  pluginArrayData.set(this, items);
  items.forEach((item, index) => {
    Object.defineProperty(this, String(index), { value: item, enumerable: true, configurable: true });
    Object.defineProperty(this, item.name, { value: item, enumerable: false, configurable: true });
  });
}
delete PluginArray.prototype.constructor;
Object.defineProperty(PluginArray.prototype, "length", {
  get() { return pluginArrayData.get(this)?.length ?? 0; }, enumerable: true, configurable: true,
});
Object.defineProperty(PluginArray.prototype, "item", {
  value: function item(index) { return this[String(index)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(PluginArray.prototype, "namedItem", {
  value: function namedItem(name) { return this[String(name)] ?? null; }, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(PluginArray.prototype, "refresh", {
  value: function refresh() {}, writable: true, configurable: true, enumerable: true,
});
Object.defineProperty(PluginArray.prototype, "constructor", {
  value: PluginArray, writable: true, configurable: true,
});
Object.defineProperty(PluginArray.prototype, Symbol.toStringTag, { value: "PluginArray" });
PluginArray.prototype[Symbol.iterator] = Array.prototype[Symbol.iterator];

const pdfDescription = "Portable Document Format";
const makePdfMimeItems = () => [
  new MimeType("application/pdf", pdfDescription, "pdf"),
  new MimeType("text/pdf", pdfDescription, "pdf"),
];
const pluginItems = [
  "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
  "Microsoft Edge PDF Viewer", "WebKit built-in PDF",
].map((name) => {
  const mimes = makePdfMimeItems();
  const plugin = new Plugin(name, pdfDescription, "internal-pdf-viewer", mimes);
  for (const mime of mimes) mimeTypeData.get(mime).enabledPlugin = plugin;
  return plugin;
});
const mimeItems = makePdfMimeItems();
for (const mime of mimeItems) mimeTypeData.get(mime).enabledPlugin = pluginItems[0];
const navigatorMimeTypes = new MimeTypeArray(mimeItems);
const navigatorPlugins = new PluginArray(pluginItems);

function Navigator() {}
markNativeFunction(Navigator, "Navigator");
delete Navigator.prototype.constructor;
const navigatorPropertyOrder = [
  "maxTouchPoints","scheduling","userActivation","geolocation","ink","doNotTrack",
  "mediaCapabilities","permissions","webkitTemporaryStorage","webkitPersistentStorage",
  "windowControlsOverlay","hardwareConcurrency","cookieEnabled","appCodeName","appName",
  "appVersion","platform","product","productSub","userAgent","vendor","vendorSub",
  "language","languages","onLine","webdriver","plugins","mimeTypes","pdfViewerEnabled",
  "connection","getGamepads","javaEnabled","sendBeacon","vibrate","mediaSession","constructor",
  "cpuPerformance","bluetooth","clipboard","credentials","keyboard","managed","mediaDevices",
  "serviceWorker","virtualKeyboard","wakeLock","deviceMemory","userAgentData","locks","storage",
  "gpu","login","devicePosture","hid","presentation","serial","usb","xr","storageBuckets",
  "canShare","share","clearAppBadge","getBattery","getUserMedia","requestMIDIAccess",
  "requestMediaKeySystemAccess","setAppBadge","webkitGetUserMedia","getInstalledRelatedApps",
  "registerProtocolHandler","unregisterProtocolHandler",
];
const navigatorMethods = {
  getGamepads: () => [], javaEnabled: () => false, sendBeacon: () => true, vibrate: () => false,
  canShare: () => false, share: () => Promise.reject(new Error("NotAllowedError")),
  clearAppBadge: () => Promise.resolve(), setAppBadge: () => Promise.resolve(),
  getBattery: () => Promise.resolve({ charging: true, level: 1 }),
  getUserMedia: () => undefined, webkitGetUserMedia: () => undefined,
  requestMIDIAccess: () => Promise.reject(new Error("NotSupportedError")),
  requestMediaKeySystemAccess: () => Promise.reject(new Error("NotSupportedError")),
  getInstalledRelatedApps: () => Promise.resolve([]),
  registerProtocolHandler: () => undefined, unregisterProtocolHandler: () => undefined,
};
const navigatorValues = {
  maxTouchPoints: MAX_TOUCH_POINTS,
  scheduling: { isInputPending: () => false },
  userActivation: { hasBeenActive: true, isActive: false },
  geolocation: { getCurrentPosition(){}, watchPosition(){ return 0; }, clearWatch(){} },
  ink: {}, doNotTrack: null, mediaCapabilities: {}, permissions: { query: async () => ({ state: "prompt" }) },
  webkitTemporaryStorage: {}, webkitPersistentStorage: {}, windowControlsOverlay: {},
  hardwareConcurrency: HARDWARE_CONCURRENCY, cookieEnabled: true, appCodeName: "Mozilla",
  appName: "Netscape", appVersion: UA.startsWith("Mozilla/") ? UA.slice(8) : UA,
  platform: NAV_PLATFORM, product: "Gecko", productSub: "20030107", userAgent: UA,
  vendor: NAV_VENDOR, vendorSub: "", language: "zh-CN", languages: Object.freeze(["zh-CN"]),
  onLine: true, webdriver: false, plugins: navigatorPlugins, mimeTypes: navigatorMimeTypes,
  pdfViewerEnabled: true, connection: { downlink: 10, effectiveType: "4g", rtt: 50, saveData: false },
  mediaSession: {}, cpuPerformance: undefined, bluetooth: {}, clipboard: {}, credentials: {}, keyboard: {},
  managed: {}, mediaDevices: {}, serviceWorker: {}, virtualKeyboard: {}, wakeLock: {},
  deviceMemory: DEVICE_MEMORY,
  userAgentData: undefined,
  locks: {}, storage: {}, gpu: {}, login: {}, devicePosture: {}, hid: {}, presentation: {}, serial: {},
  usb: {}, xr: {}, storageBuckets: {},
};
for (const name of navigatorPropertyOrder) {
  if (name === "constructor") {
    Object.defineProperty(Navigator.prototype, name, { value: Navigator, writable: true, configurable: true });
  } else if (name in navigatorMethods) {
    const method = markNativeFunction(navigatorMethods[name], name);
    Object.defineProperty(Navigator.prototype, name, { value: method, writable: true,
      configurable: true, enumerable: true });
  } else {
    const getter = markNativeFunction(() => {
      if (TRACE_ENV) ENV_READS.push(name);
      return navigatorValues[name];
    }, `get ${name}`);
    Object.defineProperty(Navigator.prototype, name, { get: getter,
      configurable: true, enumerable: true });
  }
}
Object.defineProperty(Navigator.prototype, Symbol.toStringTag, { value: "Navigator" });
const navigatorObject = new Navigator();

Object.assign(globalThis, {
  Window, Document, HTMLDocument, HTMLCanvasElement,
  CanvasRenderingContext2D, WebGLRenderingContext,
  Navigator, Plugin, PluginArray, MimeType, MimeTypeArray,
});

async function collectReq(input, init = {}) {
  const isReq = input && typeof input === "object" && typeof input.method === "string";
  const url = typeof input === "string" ? input : (input && input.url) || "";
  const method = (init.method || (isReq ? input.method : "GET")).toUpperCase();
  const headers = {};
  const pushH = (h) => {
    if (!h) return;
    if (typeof h.forEach === "function") h.forEach((v, k) => (headers[k] = v));
    else if (Array.isArray(h)) h.forEach(([k, v]) => (headers[k] = v));
    else Object.assign(headers, h);
  };
  if (isReq) pushH(input.headers);
  pushH(init.headers);
  if (!headers["user-agent"] && !headers["User-Agent"]) headers["user-agent"] = UA;
  if (!headers.referer) headers.referer = PAGE_URL;
  if (!headers["accept-language"]) headers["accept-language"] = ACCEPT_LANGUAGE;
  if (!headers["sec-ch-ua"]) headers["sec-ch-ua"] = CLIENT_HINT_UA;
  if (!headers["sec-ch-ua-mobile"]) headers["sec-ch-ua-mobile"] = CLIENT_HINT_MOBILE;
  if (!headers["sec-ch-ua-platform"]) headers["sec-ch-ua-platform"] = CLIENT_HINT_PLATFORM;
  if (!headers.accept) headers.accept = "*/*";
  if (!headers.origin) headers.origin = pageLocation.origin;
  if (!headers.priority) headers.priority = "u=1, i";
  if (!headers["sec-fetch-dest"]) headers["sec-fetch-dest"] = "empty";
  if (!headers["sec-fetch-mode"]) headers["sec-fetch-mode"] = "cors";
  if (!headers["sec-fetch-site"]) headers["sec-fetch-site"] = "same-origin";
  if (!headers.cookie && DOCUMENT_COOKIE) headers.cookie = DOCUMENT_COOKIE;
  Object.assign(headers, EDGE_HEADERS);

  let body;
  if (init.body !== undefined && init.body !== null) body = init.body;
  else if (isReq && input.body) {
    try { body = await input.clone().text(); } catch { try { body = await input.text(); } catch {} }
  }
  if (body && typeof body !== "string") body = String(body);
  return { url, method, headers, body };
}

async function fetchViaPython(url, method, headers, body) {
  const response = await realFetch(url, { method, headers, body });
  return { response, transport: response._transport || "python-embedded" };
}

async function fetchViaPersistentBridge(url, method, headers, body) {
  const response = await realFetch(PERSISTENT_BRIDGE_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-bridge-token": PERSISTENT_BRIDGE_TOKEN,
    },
    body: JSON.stringify({ url, method, headers, body }),
  });
  const result = await response.json();
  if (!response.ok || result.error) {
    throw new Error(result.error || `persistent bridge HTTP ${response.status}`);
  }
  return {
    response: new Response(result.body, {
      status: result.status,
      headers: result.headers || { "content-type": "application/json" },
    }),
    transport: result.transport || "persistent-edge-bridge",
  };
}

globalThis.fetch = async (input, init = {}) => {
  const { url, method, headers, body } = await collectReq(input, init);
  const rec = { url, method, headerNames: Object.keys(headers).sort(),
                bodyLen: body ? body.length : 0 };
  try {
    let r;
    if (MOCK_GETTOKEN && url.includes("/wasm/gettoken")) {
      r = new Response(JSON.stringify({ code: "0", data: "00000000000000000000000000000000", success: true }), {
        status: 200, headers: { "content-type": "application/json" },
      });
      rec.transport = "mock";
    } else if (!USE_NATIVE_FETCH && url.includes("/wasm/gettoken")) {
      const bridged = PERSISTENT_BRIDGE_URL
        ? await fetchViaPersistentBridge(url, method, headers, body)
        : await fetchViaPython(url, method, headers, body);
      r = bridged.response;
      rec.transport = bridged.transport;
    } else {
      r = await realFetch(url, { method, headers, body });
      rec.transport = "python-embedded";
    }
    const text = await r.text();
    rec.status = r.status;
    try {
      const parsed = JSON.parse(text);
      rec.responseCode = parsed && parsed.code;
      rec.responseSuccess = parsed && parsed.success;
      rec.responseDataLength = typeof parsed?.data === "string" ? parsed.data.length : 0;
    } catch {}
    calls.push(rec);
    return new Response(text, { status: r.status, headers: r.headers });
  } catch (e) {
    rec.error = String(e && e.message ? e.message : e);
    calls.push(rec);
    throw e;
  }
};

// ---- 浏览器环境（wasm 读取指纹用）----
Object.defineProperty(globalThis, "navigator", {
  configurable: true, writable: true,
  value: navigatorObject,
});
globalThis.window = globalThis;
globalThis.self = globalThis;
Object.defineProperty(globalThis, "screen", {
  configurable: true, writable: true,
  value: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT,
           availWidth: VIEWPORT_WIDTH, availHeight: VIEWPORT_HEIGHT,
           colorDepth: 24, pixelDepth: 24,
           orientation: {
             type: VIEWPORT_WIDTH >= VIEWPORT_HEIGHT ? "landscape-primary" : "portrait-primary",
             angle: 0,
           } },
});
globalThis.location = {
  href: pageLocation.href, origin: pageLocation.origin, protocol: pageLocation.protocol,
  host: pageLocation.host, hostname: pageLocation.hostname, port: pageLocation.port,
  pathname: pageLocation.pathname, search: pageLocation.search, hash: pageLocation.hash,
  toString() { return this.href; },
};
globalThis.document = new HTMLDocument();
try {
  const perf = globalThis.performance || {};
  if (typeof perf.markResourceTiming !== "function") perf.markResourceTiming = () => {};
  if (!perf.timing) perf.timing = { navigationStart: Date.now() - 1500, fetchStart: Date.now() - 1500 };
  if (!perf.memory) perf.memory = {
    jsHeapSizeLimit: 3760000000,
    totalJSHeapSize: 10000000,
    usedJSHeapSize: 10000000,
  };
  if (typeof perf.now !== "function") perf.now = () => Date.now();
  globalThis.performance = perf;
} catch {}
Object.assign(globalThis, {
  innerWidth: INNER_WIDTH, innerHeight: INNER_HEIGHT,
  outerWidth: VIEWPORT_WIDTH, outerHeight: VIEWPORT_HEIGHT,
  devicePixelRatio: DEVICE_PIXEL_RATIO,
  parent: globalThis,
  frameElement: null,
  isSecureContext: true,
});
const LOGIN_TIMESTAMP = Number(process.env.COC_LOGIN_TIMESTAMP_MS || Date.now());
const PHONE_TOKEN = process.env.PHONE_TOKEN || "";
const PHONE_NUMBER = process.env.COC_PHONE || "";
const PHONE_MASK = process.env.COC_PHONE_MASK ||
  (PHONE_NUMBER.length >= 7 ? `${PHONE_NUMBER.slice(0, 3)}****${PHONE_NUMBER.slice(-4)}` : "");
const AUTH_OBJECT = {
  goodsid_key: "goodsId",
  isVipPurchase: "isVipPurchase",
  user_info: {
    iswhite: 0, medalType: null, phone: PHONE_TOKEN, formPhone: PHONE_TOKEN,
    phoneMask: PHONE_MASK, provinceCode: "", numberNet: "", loginType: 7,
    cityCode: "", refreshToken: process.env.COC_REFRESH_TOKEN || "",
  },
  vip_info: null,
  page_max_vip_level: null,
  hltoken: null,
  nesstoken: null,
  login_flag: {
    isLogin: 1,
    expires: LOGIN_TIMESTAMP + 30 * 24 * 60 * 60 * 1000,
    loginTimestamp: LOGIN_TIMESTAMP,
    currentRenewTimes: 0,
  },
};
const __store = {
  gsmVipLevel: "0",
  cacheTimeStamp: String(Date.now()),
  isGsmVip: "false",
  ruleByStrategyList: "[]",
  jumpInfo: "",
  iswebp: "1",
  lsPageInfo: JSON.stringify({}),
  FirstVisit: String(Date.now()),
  bgcolor: "",
  appLoginTimeout: "true",
  application_h5: "",
  _zw_kvani5r: DFP_CACHE,
  gioTimerPerformance: "",
  canvasworkbenchweb_auth: JSON.stringify(AUTH_OBJECT),
  gioPerformanceInfo: "",
  ...(EDGE_CONTEXT?.data?.environment?.storage?.local || {}),
};
function patchAuthStore(store) {
  const raw = store.canvasworkbenchweb_auth;
  if (!raw) return;
  try {
    const auth = JSON.parse(raw);
    auth.user_info = auth.user_info || {};
    if (process.env.PHONE_TOKEN) {
      auth.user_info.phone = process.env.PHONE_TOKEN;
      auth.user_info.formPhone = process.env.PHONE_TOKEN;
    }
    store.canvasworkbenchweb_auth = JSON.stringify(auth);
  } catch {}
}
patchAuthStore(__store);
globalThis.localStorage = {
  getItem: (k) => (k in __store ? __store[k] : null),
  setItem: (k, v) => { __store[k] = String(v); },
  removeItem: (k) => { delete __store[k]; },
  key: (i) => Object.keys(__store)[i] ?? null,
  get length() { return Object.keys(__store).length; },
};
const __sstore = {
  timeDiff: "null",
  offlineTimeDiff: "",
  miniproBarShowed: "true",
  _zw_kvani5r: DFP_CACHE,
  canvasworkbenchweb_auth: JSON.stringify({ phone: PHONE_TOKEN, phoneMask: PHONE_MASK }),
  isGrey: "0",
  "risk-device-token": process.env.COC_GR_RISK_P3 || "",
  ...(EDGE_CONTEXT?.data?.environment?.storage?.session || {}),
};
patchAuthStore(__sstore);
globalThis.sessionStorage = {
  getItem: (k) => (k in __sstore ? __sstore[k] : null),
  setItem: (k, v) => { __sstore[k] = String(v); },
  removeItem: (k) => { delete __sstore[k]; },
  key: (i) => Object.keys(__sstore)[i] ?? null,
  get length() { return Object.keys(__sstore).length; },
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false, addListener(){} });
if (TRACE_ENV) {
  globalThis.__wheelObjectTrace = (kind, target, key, result) => {
    let label = Object.prototype.toString.call(target);
    if (target === globalThis) label = "globalThis";
    else if (target === navigatorObject) label = "navigator";
    else if (target === Navigator.prototype) label = "Navigator.prototype";
    else if (target === globalThis.screen) label = "screen";
    else if (target === globalThis.performance) label = "performance";
    const resultSummary = Array.isArray(result)
      ? [result.length, result.slice(0, 16)]
      : typeof result;
    traceEnv(`object.${kind}`, [label, key == null ? null : String(key), resultSummary]);
  };
}


// ---- web_sys::window() instanceof Window ----
Object.setPrototypeOf(globalThis, Window.prototype);

async function __wheel_run() {
globalThis.__wheel_stage = "wasm-init";
const mod = globalThis.__wheel_module;
if (!mod || !globalThis.__wheel_wasm_bytes) throw new Error("wheel glue/WASM 未由 Python 装载");
await mod.default(globalThis.__wheel_wasm_bytes);
globalThis.__wheel_stage = "wasm-ready";
const GT = "https://dev.coc.10086.cn/coc3/gr/risk-ability/wasm/gettoken";
const res = {
  schema_version: 1,
  config: {
    page_url: PAGE_URL, user_agent: UA, platform: NAV_PLATFORM, vendor: NAV_VENDOR,
    sec_ch_ua: CLIENT_HINT_UA, sec_ch_ua_mobile: CLIENT_HINT_MOBILE,
    sec_ch_ua_platform: CLIENT_HINT_PLATFORM, accept_language: ACCEPT_LANGUAGE,
    viewport: [VIEWPORT_WIDTH, VIEWPORT_HEIGHT], inner: [INNER_WIDTH, INNER_HEIGHT],
    device_pixel_ratio: DEVICE_PIXEL_RATIO, hardware_concurrency: HARDWARE_CONCURRENCY,
    device_memory: DEVICE_MEMORY, max_touch_points: MAX_TOUCH_POINTS,
    touch_event: "ontouchstart" in globalThis, orientation: globalThis.screen.orientation.type,
    canvas_backend: "bundled-skia-snapshot",
    wheel_min_lifetime_ms: WHEEL_MIN_LIFETIME_MS,
  },
  assets: {
    version: "0.0.8",
    glue_sha256: ASSETS.glue.sha256,
    wasm_sha256: ASSETS.wasm.sha256,
    source: "bundled",
  },
  context: {
    cookie_length: DOCUMENT_COOKIE.length,
    cookie_parts: DOCUMENT_COOKIE.split("; ").filter(Boolean).map((part) => {
      const separator = part.indexOf("=");
      return { name: part.slice(0, separator), value_length: part.length - separator - 1 };
    }),
    local_storage_keys: Object.keys(__store).length,
    session_storage_keys: Object.keys(__sstore).length,
    local_storage_value_lengths: Object.fromEntries(
      Object.entries(__store).map(([key, value]) => [key, String(value).length]),
    ),
    session_storage_value_lengths: Object.fromEntries(
      Object.entries(__sstore).map(([key, value]) => [key, String(value).length]),
    ),
    stack_summaries: STACK_SUMMARIES,
    canvas: LAST_CANVAS_SUMMARY,
    source: EDGE_CONTEXT ? "edge-capture" : "emulated",
  },
  module_url_kind: "embedded-v8",
  mock_gettoken: MOCK_GETTOKEN,
};

try {
  const wheelCreatedAt = Date.now();
  const w = mod.Wheel.create_wheel();
  globalThis.__wheel_stage = "gettoken";
  const dt = await w.g_dt_with_custom_url(GT, 15000);
  globalThis.__wheel_stage = "roto-wait";
  const remainingLifetime = WHEEL_MIN_LIFETIME_MS - (Date.now() - wheelCreatedAt);
  if (remainingLifetime > 0) {
    await new Promise((resolve) => setTimeout(resolve, remainingLifetime));
  }
  res.g_dt = typeof dt === "string" ? { len: dt.length } : { type: typeof dt };
  const wp = w.roto_simple(JSON.stringify({}));
  globalThis.__wheel_stage = "done";
  res.roto_simple = Array.isArray(wp)
    ? { count: wp.length, lens: wp.map((x) => String(x).length),
        ...(EMIT_VALUES ? { value: wp.map(String) } : {}) }
    : { type: typeof wp };
} catch (e) {
  res.error = String(e && e.message ? e.message : e);
}

res.context.canvas = LAST_CANVAS_SUMMARY;
res.context.stack_summaries = [...STACK_SUMMARIES];
res.calls = calls;
if (TRACE_ENV) res.env_reads = [...new Set(ENV_READS)];
return JSON.stringify(res);
}
