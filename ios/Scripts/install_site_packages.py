#!/usr/bin/env python3
"""Install the pure-Python third-party packages into the app's Python path.

iOS cannot build wheels for ``cryptography``/``opencv-python``/``curl_cffi``/
``numpy`` (C extensions compiled against desktop OpenSSL and glibc), which is
exactly why ``iosbridge`` supplies those layers. Everything that remains -- the
web stack and small utilities -- is pure Python and installs normally.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app/LingxiWorkspace/Resources/python/lib/python3.12/site-packages"

# Mirrors requirements.txt minus the packages iosbridge replaces:
#   cryptography      -> iosbridge.crypto   (AES/3DES/GCM/RSA/PKCS7, parity-tested)
#   opencv-python     -> iosbridge.imaging  (slider recognition, parity-tested)
#   numpy             -> only used by the desktop OpenCV slider path
#   curl_cffi         -> replaced by NativeHttp over httpx
#   mini-racer        -> JavaScriptCore via iosbridge.bridge
PACKAGES = [
    "Flask==3.1.2",
    "waitress==3.0.2",
    "httpx==0.28.1",
    "h2==4.2.0",
    "qrcode==8.2",
]


def main() -> int:
    if not TARGET.parent.parent.exists():
        print("Python framework missing; run Scripts/fetch_python_framework.sh first.",
              file=sys.stderr)
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--target", temporary,
             "--platform", "any",
             "--only-binary=:all:",
             "--no-compile",
             *PACKAGES],
            check=True,
        )
        for entry in Path(temporary).iterdir():
            destination = TARGET / entry.name
            if destination.exists():
                shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
            if entry.is_dir():
                shutil.copytree(entry, destination)
            else:
                shutil.copy2(entry, destination)
    print("installed", len(PACKAGES), "packages into", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
