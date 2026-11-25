#!/bin/sh
# Fetch a prebuilt CPython for iOS from beeware/Python-Apple-support.
#
# This is the iOS equivalent of what Chaquopy bundles into the APK: it is the
# real interpreter plus its standard library, not a reimplementation of it.
#
# Asset naming across releases is not stable, so instead of guessing a URL we
# ask the GitHub API for the newest release of the requested minor line and
# pick the iOS asset out of it.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/app/LingxiWorkspace/Resources/python"
MINOR="${PYTHON_MINOR:-3.12}"

if [ -d "$TARGET/lib/python${MINOR}" ]; then
    echo "==> python ${MINOR} already staged"
    exit 0
fi

echo "==> resolving Python-Apple-support release for ${MINOR}"
URL=$(MINOR="$MINOR" python3 - <<'PY'
import json, os, re, sys, urllib.request

minor = os.environ["MINOR"]
req = urllib.request.Request(
    "https://api.github.com/repos/beeware/Python-Apple-support/releases?per_page=100",
    headers={"User-Agent": "lingxi-ios-build", "Accept": "application/vnd.github+json"},
)
releases = json.loads(urllib.request.urlopen(req, timeout=60).read())

def rank(tag):
    """Newest build wins; 3.12-b6 > 3.12-b5 > 3.12-b1."""
    m = re.match(rf"^{re.escape(minor)}-b(\d+)$", tag)
    return int(m.group(1)) if m else -1

candidates = [r for r in releases if rank(r["tag_name"]) >= 0]
if not candidates:
    sys.exit(f"no release found for Python {minor}")

latest = max(candidates, key=lambda r: rank(r["tag_name"]))
for asset in latest["assets"]:
    if re.match(rf"^Python-{re.escape(minor)}-iOS-support\..*\.tar\.gz$", asset["name"]):
        print(asset["browser_download_url"])
        break
else:
    sys.exit(f"release {latest['tag_name']} has no iOS asset")
PY
)

if [ -z "$URL" ]; then
    echo "could not resolve a download URL" >&2
    exit 1
fi

echo "==> downloading $(basename "$URL")"
mkdir -p "$TARGET"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fL --retry 3 --retry-delay 2 "$URL" -o "$TMP/python.tar.gz"

echo "==> extracting"
tar xzf "$TMP/python.tar.gz" -C "$TMP"

# The archive either contains the payload at the top level or wraps it in a
# single directory; handle both.
if [ -d "$TMP/lib" ] || [ -d "$TMP/Python.xcframework" ]; then
    SRC="$TMP"
else
    SRC="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d ! -name 'python.tar.gz' | head -1)"
fi
if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
    echo "unexpected archive layout:" >&2
    ls -la "$TMP" >&2
    exit 1
fi

cp -R "$SRC"/. "$TARGET"/

if [ ! -d "$TARGET/lib/python${MINOR}" ]; then
    echo "archive did not provide lib/python${MINOR}" >&2
    find "$TARGET" -maxdepth 3 -type d | head -30 >&2
    exit 1
fi

echo "==> python ${MINOR} staged at $TARGET"
du -sh "$TARGET"
