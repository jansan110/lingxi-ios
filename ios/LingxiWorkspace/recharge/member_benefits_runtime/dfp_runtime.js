/**
 * Python embedded-V8 bridge for the page's original const-id.js SDK.
 *
 * stdout is always exactly one JSON document. Diagnostics go to stderr.
 * The device token is redacted unless --emit-token is explicitly supplied.
 */
(function () {

const HERE = ".";
const SCHEMA_VERSION = 1;
const SDK_VERSION = "1.1511.0";
const SDK_URL = "https://res.coc.10086.cn/res/cdn/coc1-online/fixedPath/coc3/rightsmarket-h5-canvas/js/index-private-web.js";
const SDK_SHA256 = "f267b0f6a46b2b093204762b58aa321d7a25bfc0c150dfa42a657b53b76c96bc";
const SDK_MARKER = `/*! const-id.js v${SDK_VERSION}`;
const SDK_CAPTURE = path.join(
  HERE,
  "bodies7",
  "010_es_cdn_coc1-online_fixedPath_coc3_rightsmarket-h5-canvas_js_index-private-web.js.txt",
);
const SDK_BUNDLED = path.join(HERE, "assets", "const-id", `const-id-${SDK_VERSION}.js`);
const SDK_CACHE = path.join(HERE, ".cache", `const-id-v${SDK_VERSION}.js`);
const DEFAULT_STATE = path.join(HERE, ".cache", "dfp-state.json");
const DEFAULT_PAGE_URL = "https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/online/zeromzone?channelCode=P00000011939&pageKey=3&pageRecorded=true";
const DEFAULT_SERVER = "https://yxfk.market.chinamobile.com:8081/mgt/decision-engine/udid/c1";
const DEFAULT_APP_ID = "50c71040541846d2e70e393e73271c7f";
const DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0";

const stderr = (...parts) => process.stderr.write(parts.map(String).join(" ") + "\n");
const sha256 = (value) => crypto.createHash("sha256").update(value).digest("hex");

class DfpError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "DfpError";
    this.code = code;
  }
}

function parseArgs(argv) {
  const valueFlags = new Set([
    "user-agent", "page-url", "platform", "sdk-path", "state-path",
    "app-id", "server", "timeout-ms",
  ]);
  const boolFlags = new Set(["emit-token", "mock-server", "help"]);
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const raw = argv[i];
    if (!raw.startsWith("--")) throw new DfpError("invalid_argument", `Unexpected argument: ${raw}`);
    const eq = raw.indexOf("=");
    const name = raw.slice(2, eq >= 0 ? eq : undefined);
    if (boolFlags.has(name)) {
      if (eq >= 0) throw new DfpError("invalid_argument", `Boolean flag --${name} takes no value`);
      out[name] = true;
      continue;
    }
    if (!valueFlags.has(name)) throw new DfpError("invalid_argument", `Unknown option: --${name}`);
    const value = eq >= 0 ? raw.slice(eq + 1) : argv[++i];
    if (value === undefined || value === "") throw new DfpError("invalid_argument", `Missing value for --${name}`);
    out[name] = value;
  }
  return out;
}

function envFirst(...names) {
  for (const name of names) if (process.env[name]) return process.env[name];
  return undefined;
}

function buildConfig(args) {
  const timeoutMs = Number(args["timeout-ms"] || envFirst("DFP_TIMEOUT_MS") || 2000);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60_000) {
    throw new DfpError("invalid_timeout", "timeout-ms must be an integer between 100 and 60000");
  }
  const userAgent = args["user-agent"] || envFirst("COC_USER_AGENT", "DFP_USER_AGENT") || DEFAULT_UA;
  const pageUrl = args["page-url"] || envFirst("COC_PAGE_URL", "DFP_PAGE_URL") || DEFAULT_PAGE_URL;
  const platform = args.platform || envFirst("COC_NAVIGATOR_PLATFORM", "COC_PLATFORM", "DFP_PLATFORM") ||
    (process.platform === "win32" ? "Win32" : process.platform === "darwin" ? "MacIntel" : "Linux x86_64");
  try { new URL(pageUrl); } catch { throw new DfpError("invalid_page_url", `Invalid page URL: ${pageUrl}`); }
  const mockServer = Boolean(args["mock-server"] || envFirst("DFP_MOCK_SERVER") === "1");
  const defaultStatePath = mockServer
    ? path.join(HERE, ".cache", "dfp-state.mock.json")
    : DEFAULT_STATE;
  return {
    userAgent,
    pageUrl,
    platform,
    timeoutMs,
    emitToken: Boolean(args["emit-token"]),
    mockServer,
    debug: envFirst("DFP_DEBUG") === "1",
    sdkPath: args["sdk-path"] || envFirst("DFP_SDK_PATH"),
    statePath: path.resolve(args["state-path"] || envFirst("DFP_STATE_PATH") || defaultStatePath),
    appId: args["app-id"] || envFirst("DFP_APP_ID") || DEFAULT_APP_ID,
    server: args.server || envFirst("DFP_SERVER") || DEFAULT_SERVER,
  };
}

function normalizeSdk(bytes, label) {
  const text = Buffer.isBuffer(bytes) ? bytes.toString("utf8") : String(bytes);
  const markerAt = text.indexOf(SDK_MARKER);
  if (markerAt < 0) throw new DfpError("sdk_version_mismatch", `${label} is not const-id.js v${SDK_VERSION}`);
  const source = text.slice(markerAt);
  const digest = sha256(Buffer.from(source, "utf8"));
  if (digest !== SDK_SHA256) {
    throw new DfpError("sdk_hash_mismatch", `${label} SHA-256 ${digest} does not match pinned ${SDK_SHA256}`);
  }
  return source;
}

function ensurePrivateDirectory(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch {}
}

function atomicWrite(file, data, mode = 0o600) {
  ensurePrivateDirectory(path.dirname(file));
  const tmp = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(5).toString("hex")}.tmp`);
  fs.writeFileSync(tmp, data, { mode });
  try {
    fs.renameSync(tmp, file);
  } catch {
    fs.writeFileSync(file, data, { mode });
    try { fs.unlinkSync(tmp); } catch {}
  }
  try { fs.chmodSync(file, mode); } catch {}
}

async function loadSdk(config) {
  if (config.sdkPath) {
    const resolved = path.resolve(config.sdkPath);
    return { source: normalizeSdk(fs.readFileSync(resolved), resolved), origin: "explicit", path: resolved };
  }
  if (fs.existsSync(SDK_BUNDLED)) {
    return { source: normalizeSdk(fs.readFileSync(SDK_BUNDLED), SDK_BUNDLED), origin: "bundled", path: SDK_BUNDLED };
  }
  if (fs.existsSync(SDK_CACHE)) {
    return { source: normalizeSdk(fs.readFileSync(SDK_CACHE), SDK_CACHE), origin: "cache", path: SDK_CACHE };
  }
  if (fs.existsSync(SDK_CAPTURE)) {
    const source = normalizeSdk(fs.readFileSync(SDK_CAPTURE), SDK_CAPTURE);
    atomicWrite(SDK_CACHE, source, 0o600);
    return { source, origin: "captured-artifact", path: SDK_CAPTURE };
  }
  const response = await fetch(SDK_URL, { headers: { "User-Agent": config.userAgent, Accept: "application/javascript,*/*;q=0.8" } });
  if (!response.ok) throw new DfpError("sdk_download_failed", `SDK download returned HTTP ${response.status}`);
  const source = normalizeSdk(Buffer.from(await response.arrayBuffer()), SDK_URL);
  atomicWrite(SDK_CACHE, source, 0o600);
  return { source, origin: "download", path: SDK_CACHE };
}

function configFingerprint(config) {
  return sha256(JSON.stringify({
    sdk: SDK_SHA256,
    userAgent: config.userAgent,
    pageUrl: config.pageUrl,
    platform: config.platform,
  }));
}

function loadState(config) {
  const fingerprint = configFingerprint(config);
  if (!fs.existsSync(config.statePath)) return { cache: "", fingerprint, reusable: false, reason: "missing" };
  try {
    const parsed = JSON.parse(fs.readFileSync(config.statePath, "utf8"));
    if (parsed?.schema_version !== SCHEMA_VERSION) return { cache: "", fingerprint, reusable: false, reason: "schema" };
    if (parsed?.sdk_sha256 !== SDK_SHA256) return { cache: "", fingerprint, reusable: false, reason: "sdk" };
    if (parsed?.config_fingerprint !== fingerprint) return { cache: "", fingerprint, reusable: false, reason: "config" };
    const cache = typeof parsed.cache === "string" ? parsed.cache : "";
    // The SDK first stores a 45-character local id. Some previously validated
    // browser sessions carry a 72-character lid and take the 204-byte fast path.
    if (![45, 72].includes(cache.length) || !/^[\x21-\x7e]+$/.test(cache)) {
      return { cache: "", fingerprint, reusable: false, reason: "invalid" };
    }
    return { cache, fingerprint, reusable: true, reason: "ok" };
  } catch {
    return { cache: "", fingerprint, reusable: false, reason: "unreadable" };
  }
}

function saveState(config, fingerprint, cache) {
  if (typeof cache !== "string" || ![45, 72].includes(cache.length)) return false;
  const body = JSON.stringify({
    schema_version: SCHEMA_VERSION,
    sdk_version: SDK_VERSION,
    sdk_sha256: SDK_SHA256,
    config_fingerprint: fingerprint,
    cache,
    updated_at: new Date().toISOString(),
  }, null, 2) + "\n";
  atomicWrite(config.statePath, body, 0o600);
  return true;
}

class MemoryStorage {
  constructor(seed = {}) { this.map = new Map(Object.entries(seed)); }
  get length() { return this.map.size; }
  getItem(key) { return this.map.has(String(key)) ? this.map.get(String(key)) : null; }
  setItem(key, value) {
    this.map.set(String(key), String(value));
    if (process.env.DFP_DEBUG === "1") stderr("[storage]", String(key), `length=${String(value).length}`);
  }
  removeItem(key) { this.map.delete(String(key)); }
  clear() { this.map.clear(); }
  key(index) { return [...this.map.keys()][index] ?? null; }
}

class CookieJar {
  constructor(seed = {}) { this.map = new Map(Object.entries(seed)); }
  get value() { return [...this.map].map(([k, v]) => `${k}=${v}`).join("; "); }
  set value(raw) {
    const first = String(raw).split(";", 1)[0];
    const at = first.indexOf("=");
    if (at <= 0) return;
    const key = first.slice(0, at).trim();
    const value = first.slice(at + 1).trim();
    if (value) this.map.set(key, value); else this.map.delete(key);
  }
  get(name) { return this.map.get(name) ?? ""; }
}

class ElementStub {
  constructor(tagName = "div") {
    this.tagName = String(tagName).toUpperCase();
    this.style = {};
    this.children = [];
    this.parentNode = null;
    this.innerHTML = "";
    this.textContent = "";
    this.className = "";
    this.id = "";
    this.src = "";
    this.type = "";
  }
  get offsetWidth() { return 100; }
  get offsetHeight() { return 20; }
  get clientWidth() { return 100; }
  get clientHeight() { return 20; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((x) => x !== child); child.parentNode = null; return child; }
  setAttribute(name, value) { this[String(name)] = String(value); }
  getAttribute(name) { return Object.hasOwn(this, String(name)) ? this[String(name)] : null; }
  removeAttribute(name) { delete this[String(name)]; }
  getElementsByTagName() { return []; }
  canPlayType() { return ""; }
}

class Canvas2DStub {
  constructor() { this.fillStyle = "#000000"; this.strokeStyle = "#000000"; this.font = "10px sans-serif"; this.textBaseline = "alphabetic"; }
  fillRect() {}
  rect() {}
  fillText() {}
  beginPath() {}
  arc() {}
  closePath() {}
  fill() {}
  stroke() {}
  isPointInPath() { return false; }
  measureText() { return { width: 10 }; }
  getImageData() { return { data: new Uint8ClampedArray(4) }; }
  createLinearGradient() { return { addColorStop() {} }; }
}

const WEBGL_EXTENSIONS = [
  "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_clip_control", "EXT_color_buffer_half_float",
  "EXT_depth_clamp", "EXT_disjoint_timer_query", "EXT_float_blend", "EXT_frag_depth",
  "EXT_polygon_offset_clamp", "EXT_shader_texture_lod", "EXT_texture_compression_bptc",
  "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic", "EXT_texture_mirror_clamp_to_edge", "EXT_sRGB",
  "KHR_parallel_shader_compile", "OES_element_index_uint", "OES_fbo_render_mipmap", "OES_standard_derivatives", "OES_texture_float",
  "OES_texture_float_linear", "OES_texture_half_float", "OES_texture_half_float_linear",
  "OES_vertex_array_object", "WEBGL_blend_func_extended", "WEBGL_color_buffer_float",
  "WEBGL_compressed_texture_s3tc", "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
  "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers", "WEBGL_lose_context",
  "WEBGL_multi_draw", "WEBGL_polygon_mode",
];

class WebGLStub {
  constructor(canvas) {
    this.canvas = canvas;
    Object.assign(this, {
      VENDOR: 0x1F00, RENDERER: 0x1F01, VERSION: 0x1F02, SHADING_LANGUAGE_VERSION: 0x8B8C,
      MAX_TEXTURE_SIZE: 0x0D33, MAX_CUBE_MAP_TEXTURE_SIZE: 0x851C, MAX_VERTEX_ATTRIBS: 0x8869,
      MAX_TEXTURE_IMAGE_UNITS: 0x8872, MAX_VERTEX_TEXTURE_IMAGE_UNITS: 0x8B4C,
      MAX_COMBINED_TEXTURE_IMAGE_UNITS: 0x8B4D, MAX_VERTEX_UNIFORM_VECTORS: 0x8DFB,
      MAX_VARYING_VECTORS: 0x8DFC, MAX_FRAGMENT_UNIFORM_VECTORS: 0x8DFD,
      DEPTH_BITS: 0x0D56, STENCIL_BITS: 0x0D57, RED_BITS: 0x0D52, GREEN_BITS: 0x0D53,
      BLUE_BITS: 0x0D54, ALPHA_BITS: 0x0D55, FLOAT: 0x1406, ARRAY_BUFFER: 0x8892,
      STATIC_DRAW: 0x88E4, VERTEX_SHADER: 0x8B31, FRAGMENT_SHADER: 0x8B30, TRIANGLE_STRIP: 0x0005,
      HIGH_FLOAT: 0x8DF2, MEDIUM_FLOAT: 0x8DF1, LOW_FLOAT: 0x8DF0,
    });
  }
  getSupportedExtensions() { return [...WEBGL_EXTENSIONS]; }
  getExtension(name) {
    if (name === "WEBGL_debug_renderer_info") return { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
    if (name?.includes("texture_filter_anisotropic")) return { MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84FF };
    return null;
  }
  getParameter(code) {
    const strings = {
      [this.VENDOR]: "WebKit",
      [this.RENDERER]: "WebKit WebGL",
      [this.VERSION]: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
      [this.SHADING_LANGUAGE_VERSION]: "WebGL GLSL ES 1.0",
      0x9245: "Google Inc. (NVIDIA)",
      0x9246: "ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti (0x00002D04) Direct3D11 vs_5_0 ps_5_0, D3D11)",
    };
    if (Object.hasOwn(strings, code)) return strings[code];
    const numbers = {
      [this.MAX_TEXTURE_SIZE]: 16384, [this.MAX_CUBE_MAP_TEXTURE_SIZE]: 16384,
      [this.MAX_VERTEX_ATTRIBS]: 16, [this.MAX_TEXTURE_IMAGE_UNITS]: 16,
      [this.MAX_VERTEX_TEXTURE_IMAGE_UNITS]: 16, [this.MAX_COMBINED_TEXTURE_IMAGE_UNITS]: 32,
      [this.MAX_VERTEX_UNIFORM_VECTORS]: 4095, [this.MAX_VARYING_VECTORS]: 30,
      [this.MAX_FRAGMENT_UNIFORM_VECTORS]: 1024, [this.DEPTH_BITS]: 24,
      [this.STENCIL_BITS]: 0, [this.RED_BITS]: 8, [this.GREEN_BITS]: 8,
      [this.BLUE_BITS]: 8, [this.ALPHA_BITS]: 8, 0x84FF: 16,
    };
    return Object.hasOwn(numbers, code) ? numbers[code] : null;
  }
  getShaderPrecisionFormat() { return { precision: 23, rangeMin: 127, rangeMax: 127 }; }
  getContextAttributes() { return { alpha: true, antialias: true, depth: true, stencil: false }; }
  createBuffer() { return {}; }
  bindBuffer() {}
  bufferData() {}
  createProgram() { return {}; }
  createShader() { return {}; }
  shaderSource() {}
  compileShader() {}
  attachShader() {}
  linkProgram() {}
  useProgram() {}
  getAttribLocation() { return 0; }
  getUniformLocation() { return {}; }
  enableVertexAttribArray() {}
  vertexAttribPointer() {}
  uniform2f() {}
  drawArrays() {}
}

class CanvasStub extends ElementStub {
  constructor() { super("canvas"); this.width = 300; this.height = 150; }
  getContext(type) {
    if (type === "2d") return new Canvas2DStub();
    if (type === "webgl" || type === "experimental-webgl") return new WebGLStub(this);
    return null;
  }
  toDataURL() { return "data:image/png;base64,iVBORw0KGgo="; }
}

function makeArrayLike(items = []) {
  const value = [...items];
  value.item = (index) => value[index] ?? null;
  value.namedItem = (name) => value.find((item) => item?.name === name || item?.type === name) ?? null;
  return value;
}

function createPdfPluginEnvironment() {
  const description = "Portable Document Format";
  const mimeTypes = makeArrayLike([
    { type: "application/pdf", description, suffixes: "pdf", enabledPlugin: null },
    { type: "text/pdf", description, suffixes: "pdf", enabledPlugin: null },
  ]);
  const plugins = makeArrayLike([
    "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
    "Microsoft Edge PDF Viewer", "WebKit built-in PDF",
  ].map((name) => {
    const plugin = { name, description, filename: "internal-pdf-viewer", length: 2,
      0: mimeTypes[0], 1: mimeTypes[1] };
    plugin.item = (index) => plugin[index] ?? null;
    plugin.namedItem = (type) => mimeTypes.namedItem(type);
    return plugin;
  }));
  for (const mime of mimeTypes) mime.enabledPlugin = plugins[0];
  return { plugins, mimeTypes };
}

function createDocument(cookieJar) {
  const head = new ElementStub("head");
  const body = new ElementStub("body");
  const documentElement = new ElementStub("html");
  Object.defineProperties(documentElement, {
    clientWidth: { value: 390, configurable: true },
    clientHeight: { value: 844, configurable: true },
  });
  const byId = new Map();
  const document = {
    readyState: "complete",
    referrer: "",
    title: "中国移动",
    body,
    head,
    documentElement,
    createElement(tag) {
      const element = String(tag).toLowerCase() === "canvas" ? new CanvasStub() : new ElementStub(tag);
      const originalSet = element.setAttribute.bind(element);
      element.setAttribute = (name, value) => { originalSet(name, value); if (String(name) === "id") byId.set(String(value), element); };
      return element;
    },
    getElementById(id) { return byId.get(String(id)) ?? null; },
    getElementsByTagName(tag) {
      const name = String(tag).toLowerCase();
      if (name === "head") return [head];
      if (name === "body") return [body];
      return [];
    },
    getElementsByClassName() { return []; },
    addEventListener() {},
    removeEventListener() {},
    attachEvent() {},
  };
  Object.defineProperty(document, "cookie", { get: () => cookieJar.value, set: (value) => { cookieJar.value = value; } });
  return document;
}

function createMockFetch(cacheWasReusable) {
  let valid = cacheWasReusable;
  const token = "6af70000" + "m".repeat(32);
  return async (_url, init = {}) => {
    const headers = new Headers(init.headers || {});
    const param = headers.get("param") || "";
    let body;
    if (param.length > 500) {
      valid = true;
      body = { status: 2, data: token, msg: "mock token generated" };
    } else if (param.length === 204 && valid) {
      body = { status: 1, data: token, msg: "mock token valid" };
    } else {
      body = { status: -6, msg: "mock full fingerprint required" };
    }
    const responseHeaders = { "content-type": "application/json" };
    if (body.status === 1 || body.status === 2) responseHeaders.etag = token;
    return new Response(JSON.stringify(body), { status: 200, headers: responseHeaders });
  };
}

function createLegacyCompatibleFetch() {
  return (url, init = {}) => new Promise((resolve, reject) => {
    const target = new URL(url);
    if (target.protocol !== "https:") {
      reject(new DfpError("unsupported_protocol", `Only HTTPS is supported: ${target.protocol}`));
      return;
    }
    const headers = Object.fromEntries(new Headers(init.headers || {}).entries());
    const request = https.request(target, {
      method: init.method || "GET",
      headers,
      // The target server requires legacy TLS renegotiation. Keep normal
      // certificate and hostname verification enabled; only permit the
      // OpenSSL legacy-server-connect compatibility bit for this request.
      secureOptions: crypto.constants.SSL_OP_LEGACY_SERVER_CONNECT,
      signal: init.signal,
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      response.on("end", () => {
        resolve(new Response(Buffer.concat(chunks), {
          status: response.statusCode || 500,
          statusText: response.statusMessage || "",
          headers: response.headers,
        }));
      });
    });
    request.on("error", reject);
    if (init.body != null) request.write(init.body);
    request.end();
  });
}

function makeXhrClass(config, fetchImpl, records) {
  return class FetchXMLHttpRequest {
    constructor() {
      this.withCredentials = true;
      this.onload = null;
      this.onerror = null;
      this.ontimeout = null;
      this.onreadystatechange = null;
      this.readyState = 0;
      this.status = 0;
      this.statusText = "";
      this.responseText = "";
      this.response = "";
      this.responseType = "";
      this.responseURL = "";
      this.timeout = 0;
      this.requestHeaders = new Headers();
      this.responseHeaders = new Headers();
      this.controller = null;
      this.aborted = false;
    }
    open(method, url, async = true) {
      this.method = String(method).toUpperCase();
      this.url = String(url);
      this.async = async;
      this.readyState = 1;
      this.onreadystatechange?.();
    }
    setRequestHeader(name, value) { this.requestHeaders.set(String(name), String(value)); }
    getResponseHeader(name) {
      const value = this.responseHeaders.get(String(name));
      if (config.debug) stderr("[response header]", String(name), `length=${String(value || "").length}`);
      return value;
    }
    getAllResponseHeaders() { return [...this.responseHeaders].map(([k, v]) => `${k}: ${v}`).join("\r\n"); }
    abort() { this.aborted = true; this.controller?.abort(); }
    send(body = null) {
      const headers = new Headers(this.requestHeaders);
      if (!headers.has("User-Agent")) headers.set("User-Agent", config.userAgent);
      if (!headers.has("Referer")) headers.set("Referer", config.pageUrl);
      if (!headers.has("Accept-Language")) headers.set("Accept-Language", "zh-CN");
      const param = headers.get("param") || "";
      const record = {
        method: this.method,
        url: this.url,
        app_id_length: (headers.get("appid") || "").length,
        param_length: param.length,
        http_status: null,
        business_status: null,
        response_type: this.responseType || "text",
      };
      records.push(record);
      this.controller = new AbortController();
      const timeoutMs = this.timeout > 0 ? this.timeout : config.timeoutMs;
      const timer = setTimeout(() => this.controller.abort(), timeoutMs);
      fetchImpl(this.url, { method: this.method, headers, body: body == null ? undefined : body, signal: this.controller.signal })
        .then(async (response) => {
          clearTimeout(timer);
          if (this.aborted) return;
          this.status = response.status;
          this.statusText = response.statusText;
          this.responseURL = response.url || this.url;
          this.responseHeaders = new Headers(response.headers);
          this.responseText = await response.text();
          if (this.responseType === "json") {
            try { this.response = JSON.parse(this.responseText); } catch { this.response = null; }
          } else if (this.responseType === "arraybuffer") {
            this.response = new TextEncoder().encode(this.responseText).buffer;
          } else {
            this.response = this.responseText;
          }
          this.readyState = 4;
          record.http_status = response.status;
          try { record.business_status = JSON.parse(this.responseText)?.status ?? null; } catch {}
          this.onreadystatechange?.();
          this.onload?.();
        })
        .catch((error) => {
          clearTimeout(timer);
          this.readyState = 4;
          this.status = 0;
          record.error = error?.name === "AbortError" ? "timeout" : String(error?.message || error);
          this.onreadystatechange?.();
          if (error?.name === "AbortError") this.ontimeout?.(); else this.onerror?.(error);
        });
    }
  };
}

function createSandbox(config, state, fetchImpl, records) {
  const storageSeed = state.cache ? { _zw_kvani5r: state.cache } : {};
  const localStorage = new MemoryStorage(storageSeed);
  const sessionStorage = new MemoryStorage();
  const cookieJar = new CookieJar(storageSeed);
  const page = new URL(config.pageUrl);
  const pdf = createPdfPluginEnvironment();
  const silentConsole = {
    log: (...args) => { if (config.debug) stderr("[sdk]", ...args); },
    warn: (...args) => { if (config.debug) stderr("[sdk warn]", ...args); },
    error: (...args) => { if (config.debug) stderr("[sdk error]", ...args); },
    info: (...args) => { if (config.debug) stderr("[sdk info]", ...args); },
  };
  const sandbox = {
    console: silentConsole,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    URL,
    Headers,
    Response,
    Request,
    AbortController,
    Uint8Array,
    Uint8ClampedArray,
    ArrayBuffer,
    DataView,
    TextEncoder,
    TextDecoder,
    atob,
    btoa,
    crypto: globalThis.crypto,
    location: page,
    screen: { width: 390, height: 844, availWidth: 390, availHeight: 844, colorDepth: 24, pixelDepth: 24 },
    innerWidth: 980,
    innerHeight: 2121,
    outerWidth: 390,
    outerHeight: 844,
    devicePixelRatio: 2,
    history: { length: 1 },
    localStorage,
    sessionStorage,
    indexedDB: undefined,
    openDatabase: undefined,
    external: { toString: () => "[object External]" },
    matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
    addEventListener() {},
    removeEventListener() {},
    attachEvent() {},
  };
  sandbox.navigator = {
    userAgent: config.userAgent,
    appCodeName: "Mozilla",
    appName: "Netscape",
    appVersion: config.userAgent.startsWith("Mozilla/") ? config.userAgent.slice(8) : config.userAgent,
    product: "Gecko",
    productSub: "20030107",
    vendor: "Google Inc.",
    vendorSub: "",
    language: "zh-CN",
    languages: ["zh-CN"],
    platform: config.platform,
    hardwareConcurrency: os.cpus().length || 4,
    deviceMemory: undefined,
    maxTouchPoints: 1,
    msMaxTouchPoints: 0,
    cookieEnabled: true,
    onLine: true,
    webdriver: false,
    pdfViewerEnabled: true,
    plugins: pdf.plugins,
    mimeTypes: pdf.mimeTypes,
    javaEnabled: () => false,
    geolocation: { getCurrentPosition: (_ok, fail) => fail?.({ code: 1, message: "denied" }) },
    mediaDevices: { enumerateDevices: async () => [] },
  };
  sandbox.document = createDocument(cookieJar);
  sandbox.Image = class ImageStub {
    set src(value) { this._src = value; setTimeout(() => this.onerror?.(new Error("image disabled")), 0); }
    get src() { return this._src || ""; }
  };
  sandbox.XMLHttpRequest = makeXhrClass(config, fetchImpl, records);
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, localStorage, cookieJar };
}

async function runSdk(sdkSource, config, state) {
  const records = [];
  const fetchImpl = config.mockServer ? createMockFetch(state.reusable) : globalThis.fetch;
  const env = createSandbox(config, state, fetchImpl, records);
  for (const [name, value] of Object.entries(env.sandbox)) {
    if (!["window", "self", "globalThis"].includes(name)) globalThis[name] = value;
  }
  globalThis.window = globalThis;
  globalThis.self = globalThis;
  (0, eval)(sdkSource + `\n//# sourceURL=const-id-v${SDK_VERSION}.js\n`);
  if (!globalThis._zw || typeof globalThis._zw.ConstID !== "function") {
    throw new DfpError("sdk_entry_missing", "Verified SDK did not export _zw.ConstID");
  }
  const token = await new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new DfpError("sdk_timeout", "ConstID callback timed out")); }
    }, Math.max(10_000, config.timeoutMs * 4));
    try {
      globalThis._zw.ConstID({ appId: config.appId, server: config.server, timeout: config.timeoutMs }, (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(new DfpError("sdk_rejected", typeof error === "string" ? error : JSON.stringify(error)));
        else resolve(String(value || ""));
      });
    } catch (error) {
      settled = true;
      clearTimeout(timer);
      reject(error);
    }
  });
  // The SDK commits its durable lid/cache immediately after resolving the
  // callback; give that queued write one turn before reading storage.
  await new Promise((resolve) => setTimeout(resolve, 25));
  const cache = env.localStorage.getItem("_zw_kvani5r") || env.cookieJar.get("_zw_kvani5r") || "";
  return { token, cache, records };
}
globalThis.__dfp_runtime_run = async (sdkSource, config, state) =>
  JSON.stringify(await runSdk(String(sdkSource), config, state));
})();
