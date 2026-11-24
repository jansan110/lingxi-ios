#!/bin/sh
# Build an unsigned .ipa. This is the entire CI logic in one file so that the
# workflow definition upstream stays tiny (see .github/workflows/build-ipa.yml).
#
# Runs on a macOS runner with Xcode installed; a Mach-O ARM64 binary cannot be
# produced anywhere else.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/app"

echo "==> environment"
xcodebuild -version
xcrun --sdk iphoneos --show-sdk-version
sw_vers

echo "==> staging business layer"
sh "$ROOT/Scripts/stage_business.sh"

echo "==> fetching CPython for iOS"
sh "$ROOT/Scripts/fetch_python_framework.sh"

echo "==> installing pure-Python dependencies"
python3 "$ROOT/Scripts/install_site_packages.py"

echo "==> verifying payload"
B="$APP_DIR/LingxiWorkspace/Resources/business"
P="$APP_DIR/LingxiWorkspace/Resources/python"
for f in app.py ios_runtime.py java.py cryptography_shim.py; do
    test -f "$B/$f" || { echo "missing $f"; exit 1; }
done
test -d "$B/templates" || { echo "missing templates"; exit 1; }
test -d "$B/static" || { echo "missing static"; exit 1; }
test -d "$B/iosbridge" || { echo "missing iosbridge"; exit 1; }
test -d "$P/lib/python3.12" || { echo "missing python stdlib"; exit 1; }
echo "payload ok"
du -sh "$B" "$P"

echo "==> building"
cd "$APP_DIR"
set -o pipefail
xcodebuild \
    -project LingxiWorkspace.xcodeproj \
    -scheme LingxiWorkspace \
    -configuration Release \
    -sdk iphoneos \
    -destination 'generic/platform=iOS' \
    -derivedDataPath build \
    CODE_SIGNING_ALLOWED=NO \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGN_IDENTITY="" \
    DEVELOPMENT_TEAM="" \
    ONLY_ACTIVE_ARCH=NO \
    ARCHS=arm64 \
    build 2>&1 | tee build.log | tail -60

APP="build/Build/Products/Release-iphoneos/LingxiWorkspace.app"
test -d "$APP" || { echo "app bundle not produced"; tail -80 build.log; exit 1; }
file "$APP/LingxiWorkspace" || true
lipo -info "$APP/LingxiWorkspace" || true

echo "==> packaging unsigned .ipa"
rm -rf Payload LingxiWorkspace-unsigned.ipa
mkdir -p Payload
cp -R "$APP" Payload/
# ditto preserves the extended attributes and symlinks a bundle needs.
ditto -c -k --sequesterRsrc --keepParent Payload/LingxiWorkspace.app LingxiWorkspace-unsigned.ipa
ls -lh LingxiWorkspace-unsigned.ipa
unzip -l LingxiWorkspace-unsigned.ipa | head -12

echo "==> done: $APP_DIR/LingxiWorkspace-unsigned.ipa"
