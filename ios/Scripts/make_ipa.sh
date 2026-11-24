#!/bin/sh
# Turn a built LingxiWorkspace.app into an installable .ipa, optionally signed.
#
#   ./make_ipa.sh unsigned
#       -> LingxiWorkspace-unsigned.ipa  (you sign it with your own certificate)
#
#   ./make_ipa.sh sign "Apple Distribution: NAME (TEAMID)" /path/to/profile.mobileprovision
#       -> LingxiWorkspace-signed.ipa
#
# Runs on macOS. The unsigned path works on Linux too if the .app already
# exists (it is only zip + folder layout), but a .app can only be *built* on
# macOS.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${APP_PATH:-$ROOT/app/build/Build/Products/Release-iphoneos/LingxiWorkspace.app}"
MODE="${1:-unsigned}"

if [ ! -d "$APP" ]; then
    echo "app bundle not found: $APP" >&2
    echo "build it first:  cd $ROOT/app && xcodebuild -project LingxiWorkspace.xcodeproj \\" >&2
    echo "                   -scheme LingxiWorkspace -configuration Release -sdk iphoneos \\" >&2
    echo "                   -destination 'generic/platform=iOS' -derivedDataPath build \\" >&2
    echo "                   CODE_SIGNING_ALLOWED=NO build" >&2
    exit 1
fi

echo "==> app: $APP"
file "$APP/LingxiWorkspace" 2>/dev/null || true

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/Payload"
cp -R "$APP" "$WORK/Payload/"

case "$MODE" in
    unsigned)
        OUT="$ROOT/LingxiWorkspace-unsigned.ipa"
        if command -v ditto >/dev/null 2>&1; then
            ( cd "$WORK" && ditto -c -k --sequesterRsrc --keepParent Payload/LingxiWorkspace.app "$OUT" )
        else
            ( cd "$WORK" && zip -qry "$OUT" Payload )
        fi
        echo "==> $OUT"
        ls -lh "$OUT"
        cat <<'MSG'

This .ipa is unsigned. To install it on a device you must sign it with a
certificate the device trusts (a 7-day free Apple ID certificate or a paid
developer certificate). Tools that do this on-device or on a desktop:
  - Sideloadly / AltStore / TrollStore (on-device or via USB)
  - `codesign` + `ldid` if you prefer the command line
MSG
        ;;

    sign)
        IDENTITY="${2:-}"
        PROFILE="${3:-}"
        if [ -z "$IDENTITY" ] || [ -z "$PROFILE" ] || [ ! -f "$PROFILE" ]; then
            echo 'usage: ./make_ipa.sh sign "Apple Distribution: NAME (TEAMID)" /path/to/profile.mobileprovision' >&2
            exit 1
        fi
        if ! command -v codesign >/dev/null 2>&1; then
            echo "codesign not found; run this on macOS" >&2
            exit 1
        fi

        echo "==> provisioning profile"
        cp "$PROFILE" "$WORK/Payload/LingxiWorkspace.app/embedded.mobileprovision"

        # Entitlements must match the profile or install will fail at launch.
        ENT="$WORK/entitlements.plist"
        security cms -D -i "$PROFILE" > "$WORK/profile.plist"
        /usr/libexec/PlistBuddy -x -c 'Print :Entitlements' "$WORK/profile.plist" > "$ENT"

        echo "==> codesign frameworks and dylibs"
        find "$WORK/Payload/LingxiWorkspace.app" \
            \( -name "*.framework" -o -name "*.dylib" \) -print0 2>/dev/null |
        while IFS= read -r -d '' item; do
            codesign --force --sign "$IDENTITY" --timestamp=none "$item"
        done

        echo "==> codesign app"
        codesign --force --sign "$IDENTITY" \
            --entitlements "$ENT" \
            --timestamp=none \
            --generate-entitlement-der \
            "$WORK/Payload/LingxiWorkspace.app"

        codesign --verify --deep --strict --verbose=2 "$WORK/Payload/LingxiWorkspace.app"

        OUT="$ROOT/LingxiWorkspace-signed.ipa"
        ( cd "$WORK" && ditto -c -k --sequesterRsrc --keepParent Payload/LingxiWorkspace.app "$OUT" )
        echo "==> $OUT"
        ls -lh "$OUT"
        ;;

    *)
        echo "usage: ./make_ipa.sh [unsigned|sign <identity> <profile>]" >&2
        exit 1
        ;;
esac
