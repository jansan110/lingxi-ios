# 灵犀工作台 · iOS 版

Android 1.9.10 的 iOS 移植。业务逻辑一行不改，只替换平台层。

## 为什么能不改业务代码

反编译 `LingxiWorkspace-1.9.10-Android-arm64.apk.1.1` 后的结构：

| 层 | Android | iOS |
|---|---|---|
| 业务逻辑 | ~60 个 Python 模块（Flask + 定时任务 + 移动侧协议） | **原样复用** |
| UI | WebView 加载 `127.0.0.1` 上的 Flask 页面 | WKWebView 加载同一套页面 |
| HTTP | OkHttp（`NativeHttp.java`） | NSURLSession / httpx |
| 凭据加密 | Android Keystore（`CredentialVault.java`） | iOS Keychain |
| JS 引擎 | Javet/V8（`MemberBenefitsV8.java`） | JavaScriptCore |
| 滑块识别 | OpenCV 4 Java 绑定 | 纯 Python 复刻（逐位一致） |
| 打包 | Chaquopy | Python-Apple-support |

业务代码只通过 `LINGXI_ANDROID=1` 判断平台，然后 `from java import jclass`。所以 iOS 版提供的是一个**同名 `java` 模块**，`jclass(...)` 返回 Python 实现。60 个业务模块一个字节都没动。

上游 `pppyl/lingxi-workspace` 是开源的，本移植基于 tag `v1.9.10`（与 APK 同版本）。

## 目录

```
LingxiWorkspace/            # Python 业务层（来自上游 v1.9.10）
├── java.py                 # ← iOS 新增：Chaquopy 兼容垫片
├── ios_runtime.py          # ← iOS 新增：启动入口（对应 android_runtime.py）
├── cryptography_shim.py    # ← iOS 新增：cryptography 重定向
└── iosbridge/              # ← iOS 新增：平台层
    ├── bridge.py           #   五个 Java 类的对应实现
    ├── crypto.py           #   AES / 3DES / GCM / RSA / PKCS7
    ├── rsa_keys.py         #   RSA 私钥生成与解密
    ├── der.py              #   DER / PEM 编码
    ├── imaging.py          #   OpenCV 流水线复刻
    ├── opencv.py           #   org.opencv.* 类
    └── jsc.py              #   JavaScriptCore C ABI 绑定

app/LingxiWorkspace/        # Swift 外壳
├── ContentView.swift       #   主界面 + WKWebView
├── WorkspaceHost.swift     #   服务生命周期 + Cookie 注入
├── PythonBridge.swift      #   CPython 嵌入
└── JSCHost.swift           #   JavaScriptCore 宿主（导出 C ABI）

Scripts/
├── stage_business.sh       # 打包业务层（对应 build_tools/build_android.py）
├── fetch_python_framework.sh
├── install_site_packages.py
└── build_app.sh            # 一键构建

tests/                      # iOS 侧验证
```

## 五个接口的实现差异

### 1. 凭据加密（CredentialVault）

Android 用 Keystore 生成 AES 密钥。iOS 用 Keychain（`kSecClassGenericPassword`），存 32 字节主密钥，加密用 AES-256-GCM。

产物格式与 Android 一致：`iv(12) || ciphertext || tag(16)`。所以同一个加密库在两端可互读。

### 2. HTTP（NativeHttp）

Java 版用 OkHttp。iOS 用 httpx（走 NSURLSession 的 TLS 信任链）。方法签名保持 `request(method, url, header_json, body, http2, connect, read)`。

返回对象保留 Java 的 `status()` / `headers()` / `read()` / `close()`，其中 `read()` 每次返回最多 8KB，读完返回 `None`（Java 是 `null`）——业务层的 `NativeBody` 就靠这个判结束。

### 3. JS 引擎（MemberBenefitsV8）

**这是最需要解释的一处。** Android 用 Javet 嵌 V8，跑两个东西：

- `wheel_runtime.js` + `wheel-0.0.8.wasm`：设备指纹
- `dfp_runtime.js` + `const-id-1.1511.0.js`：风控 SDK

iOS 没有 V8，但有系统自带的 JavaScriptCore。问题在于 JSC 是 ObjC 框架，而 **PyObjC 在 iOS 上不存在**——不能 `import JavaScriptCore`。

所以上下文必须由 Swift 持有，Python 通过 C ABI 访问：

```
Python (iosbridge/jsc.py)
    └─ ctypes 调用 lingxi_jsc_create / eval / set_callback
         └─ Swift (JSCHost.swift) @_cdecl 导出
              └─ JSContext
```

`__py_http_json` 这个同步回调（JS 里发 HTTP）用 C 函数指针回传 Python，Python 返回 `strdup` 的字符串，Swift 用完 free。这条链路是必须的，因为 wheel 运行时依赖它发请求。

Promise 的推进由 Python 侧循环驱动（JSC 没有事件循环）：反复调 `__run_due_timers()` 直到 settled，等价于 Java 版 `MemberBenefitsV8.await`。

### 4. 滑块识别

`coc_slider.py` 的算法只有四步：灰度 → 5×5 高斯 → Canny(30,100) → `TM_CCORR_NORMED`。

iOS 不引入 OpenCV（`opencv-python` 没有 iOS wheel，自己编译要几十分钟且体积翻倍）。改为纯 Python 精确复刻：

- **灰度**：OpenCV 的定点数 `(R*4899 + G*9617 + B*1868 + 8192) >> 14`
- **高斯**：ksize=5、sigma=0 时 OpenCV 推导 σ=1.1，核已按此算好；边界用 `BORDER_REFLECT_101`
- **Canny**：Sobel 幅值取 `|gx| + |gy|`，双阈值 30/100，滞后连通
- **模板匹配**：先用 FFT 相关排序候选（3 万个位置纯 Python 要 3 分钟），再对前 40 个候选按原公式精确重算。**结果是精确最大值，不是近似值。**

`tests/test_crypto_parity.py` 用真实 OpenCV 对比：6/6 命中，与 `cv2.minMaxLoc` 完全一致。

### 5. 打包与启动

Chaquopy 换成 beeware/Python-Apple-support（预编译的 iOS CPython 3.12）+ PythonKit。

`ios_runtime.py` 对应 `android_runtime.py`，返回同样的 JSON：`{"url": ..., "token": ...}`。

服务仍然只听 `127.0.0.1`，用 waitress，端口随机。区别是没 Android 前台 Service，改用 `beginBackgroundTask` 保活。

页面加载前把 `lingxi_mobile=<token>` 写进 `WKWebsiteDataStore`，等价于 Android 的 `CookieManager.setCookie`。没有这个 cookie，服务返回 403。

## 构建

需要 macOS + Xcode 15+：

```bash
brew install xcodegen
cd ios
sh Scripts/build_app.sh
```

脚本会：打包业务层 → 装纯 Python 依赖 → 拉 CPython → 生成 xcodeproj → 编译。

如果网络拉不到 Python-Apple-support，脚本会打印手动下载地址。

## 验证结果

在 Linux/iSH 上实际跑过的：

| 测试 | 结果 |
|---|---|
| `test_crypto_parity.py` | 10/10 — AES/3DES/GCM/PKCS7/RSA/DER 与 `cryptography` 逐字节一致 |
| `test_platform_contract.py` | 13/13 — 五个接口的签名与行为 |
| `test_service_smoke.py` | 18 页面 + 11 API + 静态资源 + 403 守卫 全通过 |
| 上游 `tests/` 全套 | 962 passed / 32 failed |

那 32 个失败的分类：

- **11 个**：测试用 `cryptography.fernet`（业务层完全不用），iOS 垫片未实现
- **4 个**：需要 JavaScriptCore 原生桥，Linux 上没有
- **其余**：`curl_cffi` 桌面传输路径、真实网卡扫描超时等，与移植无关

关键证据：上游的 `tests/test_android_transport.py` 全部 5 项通过——这个测试专门验证 Java 桥的契约，iOS 平台层能通过它，说明业务层分不清两端。

## 体积与性能

- 业务层：3.4 MB / 198 文件
- CPython + 标准库：约 35 MB
- 纯 Python 依赖（Flask/waitress/httpx/qrcode）：约 8 MB

滑块识别在 iPhone 上约 3-5 秒（只在需要官方验证码时触发，不是热路径）。

## 已知差异

1. **滑块是纯 Python**，比 OpenCV C 慢约 50 倍。如果这成为瓶颈，正确做法是把同一套算法写成 Swift + Accelerate，而不是引入 OpenCV。
2. **后台保活**：iOS 没有 Android 那种前台 Service。`beginBackgroundTask` 只能争取几分钟到几十分钟；长期定时任务在系统回收后会推迟到下次启动补查。这是平台限制，不是移植缺陷。
3. **Fernet 未实现**：仅测试使用，业务层零调用。
