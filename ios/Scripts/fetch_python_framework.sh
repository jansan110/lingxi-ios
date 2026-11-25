#!/bin/sh
# Fetch a prebuilt CPython for iOS from beeware/Python-Apple-support.
#
# This is the iOS equivalent of what Chaquopy bundles into the APK: the real
# interpreter plus its standard library, not a reimplementation.
#
# The asset URL is pinned rather than discovered, because GitHub's anonymous
# API rate-limits by IP and hosted runners share addresses - a discovery call
# fails intermittently with HTTP 403. Pinning makes the build reproducible.
# Override with PYTHON_SUPPORT_URL=... to move to a newer build.
#
# Two outputs, deliberately kept apart:
#   Resources/python/lib/python3.12   stdlib only, copied into the app bundle
#   Vendor/Python.xcframework         linked by Xcode, never bundled wholesale
#
# The xcframework must NOT land in Resources: it carries a testbed project and
# simulator slices (~60 MB of dead weight) that have no business in the .ipa.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/app/LingxiWorkspace/Resources/python"
VENDOR="$ROOT/app/Vendor"
MINOR="${PYTHON_MINOR:-3.12}"
URL="${PYTHON_SUPPORT_URL:-https://github.com/beeware/Python-Apple-support/releases/download/3.12-b13/Python-3.12-iOS-support.b13.tar.gz}"

if [ -d "$TARGET/lib/python${MINOR}" ] && [ -d "$VENDOR/Python.xcframework" ]; then
    echo "==> python ${MINOR} already staged"
    exit 0
fi

echo "==> downloading $(basename "$URL")"
mkdir -p "$TARGET" "$VENDOR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fL --retry 4 --retry-delay 3 --retry-all-errors \
     --connect-timeout 20 --max-time 900 \
     -o "$TMP/python.tar.gz" "$URL"

SIZE=$(wc -c < "$TMP/python.tar.gz" | tr -d ' ')
echo "==> downloaded ${SIZE} bytes"
if [ "$SIZE" -lt 10000000 ]; then
    echo "download looks truncated (${SIZE} bytes)" >&2
    head -c 400 "$TMP/python.tar.gz" >&2
    exit 1
fi

echo "==> extracting"
tar xzf "$TMP/python.tar.gz" -C "$TMP"

if [ -d "$TMP/Python.xcframework" ]; then
    SRC="$TMP"
elif [ -n "$(find "$TMP" -maxdepth 2 -name Python.xcframework -type d | head -1)" ]; then
    SRC="$(dirname "$(find "$TMP" -maxdepth 2 -name Python.xcframework -type d | head -1)")"
else
    echo "Python.xcframework not found in archive:" >&2
    find "$TMP" -maxdepth 2 | head -30 >&2
    exit 1
fi

XCF="$SRC/Python.xcframework"
if [ ! -d "$XCF/ios-arm64/Python.framework" ]; then
    echo "device slice missing in $XCF" >&2
    ls "$XCF" >&2
    exit 1
fi

# 1. The xcframework goes to Vendor/ for linking only.
rm -rf "$VENDOR/Python.xcframework"
mkdir -p "$VENDOR"
cp -R "$XCF" "$VENDOR/"
echo "==> xcframework staged at $VENDOR/Python.xcframework"

# 2. Only the standard library goes into the bundle.
STDLIB="$XCF/lib/python${MINOR}"
if [ ! -d "$STDLIB" ]; then
    echo "archive did not provide lib/python${MINOR}" >&2
    find "$XCF" -maxdepth 3 -type d -name 'python*' | head -20 >&2
    exit 1
fi

rm -rf "$TARGET/lib"
mkdir -p "$TARGET/lib"
cp -R "$STDLIB" "$TARGET/lib/python${MINOR}"

# Drop caches and test suites: they bloat the .ipa and are never imported.
find "$TARGET/lib" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$TARGET/lib" -name 'test' -type d -path '*/site-packages/*'  -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> python ${MINOR} staged at $TARGET"
du -sh "$TARGET/lib" "$VENDOR/Python.xcframework"
