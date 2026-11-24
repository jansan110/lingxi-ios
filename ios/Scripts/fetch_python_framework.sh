#!/bin/sh
# Fetch the prebuilt CPython for iOS from beeware/Python-Apple-support.
#
# This is the iOS equivalent of what Chaquopy bundles into the APK: the real
# interpreter, not a reimplementation. The release contains an xcframework plus
# the full standard library for iOS arm64.
#
# Asset names have changed across releases (".b1", ".b2", ".tar.gz" vs
# ".tar.xz"), so the name is resolved from the release API at run time instead
# of being hardcoded here. Set PYTHON_RELEASE to pin a specific tag.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/app/LingxiWorkspace/Resources/python"

if [ -d "$TARGET/lib/python3.12" ] && [ -d "$TARGET/Python.xcframework" ]; then
    echo "==> python framework already present"
    exit 0
fi

PYTHON_BIN="${PYTHON:-python3}"
mkdir -p "$TARGET"

echo "==> resolving Python-Apple-support release"
URL="$("$PYTHON_BIN" - "${PYTHON_RELEASE:-}" <<'PY'
import json
import sys
import urllib.request

pinned = sys.argv[1] if len(sys.argv) > 1 else ""
api = "https://api.github.com/repos/beeware/Python-Apple-support/releases"
if pinned:
    api += "/tags/" + pinned
else:
    api += "?per_page=20"

request = urllib.request.Request(api, headers={"User-Agent": "lingxi-ios-build"})
releases = json.loads(urllib.request.urlopen(request, timeout=60).read())
if isinstance(releases, dict):
    releases = [releases]

for release in releases:
    if release.get("draft"):
        continue
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        # The iOS support bundle, not the macOS/tvOS/watchOS ones.
        if not name.startswith("Python-") or "iOS-support" not in name:
            continue
        if not (name.endswith(".tar.gz") or name.endswith(".tar.xz") or name.endswith(".zip")):
            continue
        print(asset["browser_download_url"])
        sys.exit(0)

sys.exit("no iOS support asset found in the release list")
PY
)"

if [ -z "$URL" ]; then
    echo "could not resolve a Python-Apple-support release" >&2
    exit 1
fi
echo "    $URL"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> downloading"
case "$URL" in
    *.tar.gz)  curl -fL --retry 3 "$URL" -o "$TMP/python.tar.gz" && tar xzf "$TMP/python.tar.gz" -C "$TMP" ;;
    *.tar.xz)  curl -fL --retry 3 "$URL" -o "$TMP/python.tar.xz" && tar xJf "$TMP/python.tar.xz" -C "$TMP" ;;
    *.zip)     curl -fL --retry 3 "$URL" -o "$TMP/python.zip"    && unzip -q "$TMP/python.zip" -d "$TMP" ;;
    *)         echo "unsupported archive: $URL" >&2; exit 1 ;;
esac

# The archive has a single top-level directory; flatten it into Resources/python.
SOURCE="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
if [ -z "$SOURCE" ]; then
    echo "archive did not contain a directory" >&2
    exit 1
fi

# Newer releases nest everything under a "python" directory holding
# Python.xcframework and lib/; older ones put them at the root.
if [ -d "$SOURCE/python/Python.xcframework" ]; then
    SOURCE="$SOURCE/python"
fi

cp -R "$SOURCE"/. "$TARGET"/

# Python.xcframework keeps the stdlib inside the iOS slice for device builds.
if [ ! -d "$TARGET/lib/python3.12" ]; then
    for candidate in "$TARGET"/Python.xcframework/ios-arm64/*/lib/python3.12 \
                     "$TARGET"/Python.xcframework/ios-arm64_x86_64-simulator/*/lib/python3.12; do
        if [ -d "$candidate" ]; then
            mkdir -p "$TARGET/lib"
            cp -R "$candidate" "$TARGET/lib/"
            break
        fi
    done
fi

if [ ! -d "$TARGET/lib/python3.12" ]; then
    echo "stdlib not found after extraction; inspect $TARGET" >&2
    ls -la "$TARGET" >&2
    exit 1
fi

echo "==> python framework installed"
du -sh "$TARGET"
