#!/bin/sh
# Build the iOS app: stage the business layer, fetch CPython, then build.
#
# Requires macOS with Xcode 15+, and `xcodegen` (brew install xcodegen) the
# first time so project.yml becomes an .xcodeproj.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/4 staging business layer"
sh Scripts/stage_business.sh

echo "==> 2/4 installing third-party Python packages"
python3 Scripts/install_site_packages.py

echo "==> 3/4 fetching CPython for iOS"
sh Scripts/fetch_python_framework.sh

echo "==> 4/4 building"
if [ ! -d app/LingxiWorkspace.xcodeproj ]; then
    ( cd app && xcodegen generate )
fi
cd app
xcodebuild \
    -project LingxiWorkspace.xcodeproj \
    -scheme LingxiWorkspace \
    -configuration Release \
    -destination 'generic/platform=iOS' \
    -derivedDataPath ../.build/DerivedData \
    CODE_SIGN_IDENTITY="${CODE_SIGN_IDENTITY:--}" \
    CODE_SIGNING_REQUIRED="${CODE_SIGNING_REQUIRED:-NO}" \
    ARCHS=arm64 \
    build

echo "==> built"
find "$ROOT/.build/DerivedData/Build/Products" -name "LingxiWorkspace.app" -maxdepth 3
