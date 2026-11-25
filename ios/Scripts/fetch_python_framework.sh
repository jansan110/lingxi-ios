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
# Archive layout (3.12-b13):
#   Python.xcframework/
#     Info.plist
#     ios-arm64/            -> Python.framework (device)
#     ios-arm64_x86_64-simulator/
#     lib/python3.12/       -> standard library, shared by both slices
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/app/LingxiWorkspace/Resources/python"
MINOR="${PYTHON_MINOR:-3.12}"
URL="${PYTHON_SUPPORT_URL:-https://github.com/beeware/Python-Apple-support/releases/download/3.12-b13/Python-3.12-iOS-support.b13.tar.gz}"

if [ -d "$TARGET/lib/python${MINOR}" ]; then
    echo "==> python ${MINOR} already staged"
    exit 0
fi

echo "==> downloading $(basename "$URL")"
mkdir -p "$TARGET"
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

# Find the directory that actually holds Python.xcframework.
if [ -d "$TMP/Python.xcframework" ]; then
    SRC="$TMP"
elif [ -n "$(find "$TMP" -maxdepth 2 -name Python.xcframework -type d | head -1)" ]; then
    SRC="$(dirname "$(find "$TMP" -maxdepth 2 -name Python.xcframework -type d | head -1)")"
else
    echo "Python.xcframework not found in archive:" >&2
    find "$TMP" -maxdepth 2 | head -30 >&2
    exit 1
fi

cp -R "$SRC"/. "$TARGET"/

# The standard library lives inside the framework; surface it at the top level
# too so callers can use a single short path.
if [ -d "$TARGET/Python.xcframework/lib/python${MINOR}" ] && [ ! -d "$TARGET/lib/python${MINOR}" ]; then
    mkdir -p "$TARGET/lib"
    cp -R "$TARGET/Python.xcframework/lib/python${MINOR}" "$TARGET/lib/"
fi

if [ ! -d "$TARGET/lib/python${MINOR}" ]; then
    echo "archive did not provide lib/python${MINOR}" >&2
    find "$TARGET" -maxdepth 4 -type d -name 'python*' | head -20 >&2
    exit 1
fi

echo "==> python ${MINOR} staged at $TARGET"
du -sh "$TARGET"
ls "$TARGET"
