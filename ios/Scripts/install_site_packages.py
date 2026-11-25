#!/usr/bin/env python3
"""Install the pure-Python third-party packages into the app's Python path.

iOS cannot build wheels for ``cryptography``/``opencv-python``/``curl_cffi``/
``numpy`` (C extensions linked against desktop OpenSSL and libc), which is
exactly why ``iosbridge`` supplies those layers. Everything that remains -- the
web stack and small utilities -- is pure Python and installs normally.

MarkupSafe is the exception. It publishes no ``any`` wheel: every artifact is
built for a specific platform because of its optional C speedup, and Flask
depends on it, so a plain ``--platform any`` resolution fails outright. It does
ship a complete pure-Python fallback (``_native.py``) that ``__init__`` selects
when the extension is missing.

The resolution strategy is therefore two-phase:

  1. Install MarkupSafe from its source archive, keeping only the .py files.
  2. Install everything else with ``--no-deps`` (the dependency graph is
     already satisfied by what we placed by hand) so pip never tries to
     resolve MarkupSafe again.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_MINOR = "3.12"
TARGET = ROOT / f"app/LingxiWorkspace/Resources/python/lib/python{PY_MINOR}/site-packages"

# Mirrors requirements.txt minus the packages iosbridge replaces:
#   cryptography      -> iosbridge.crypto   (AES/3DES/GCM/RSA/PKCS7, parity-tested)
#   opencv-python     -> iosbridge.imaging  (slider recognition, parity-tested)
#   numpy             -> only used by the desktop OpenCV slider path
#   curl_cffi         -> replaced by NativeHttp over httpx
#   mini-racer        -> JavaScriptCore via iosbridge.bridge
#
# The list is explicit and complete so --no-deps is safe: every transitive
# dependency of Flask/httpx is named here.
PURE_PACKAGES = [
    # Flask stack
    "Flask==3.1.2",
    "Werkzeug==3.1.3",
    "Jinja2==3.1.6",
    "itsdangerous==2.2.0",
    "click==8.1.8",
    "blinker==1.9.0",
    # WSGI server
    "waitress==3.0.2",
    # HTTP client
    "httpx==0.28.1",
    "httpcore==1.0.9",
    "h11==0.16.0",
    "h2==4.2.0",
    "hpack==4.1.0",
    "hyperframe==6.1.0",
    "certifi==2025.10.5",
    "idna==3.11",
    "sniffio==1.3.1",
    "anyio==4.11.0",
    # QR codes
    "qrcode==8.2",
]

# Packages with a C speedup plus a pure-Python fallback: (name, version).
SOURCE_ONLY_PACKAGES = [
    ("markupsafe", "3.0.3"),
]


def run(command: list[str], quiet: bool = False) -> None:
    print("running:", " ".join(command), flush=True)
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stdout[-3000:], file=sys.stderr)
        print(completed.stderr[-4000:], file=sys.stderr)
        raise SystemExit(completed.returncode)
    if not quiet and completed.stdout.strip():
        print(completed.stdout.strip()[-800:], flush=True)


def merge(stage: Path) -> None:
    for entry in sorted(stage.iterdir()):
        destination = TARGET / entry.name
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)


def install_source_only(temporary: Path, name: str, version: str) -> None:
    """Extract the pure-Python part of a package that only ships native wheels."""
    downloads = temporary / f"src-{name}"
    downloads.mkdir()
    run([
        sys.executable, "-m", "pip", "download",
        "--no-deps", "--no-binary=:all:",
        "--dest", str(downloads),
        f"{name}=={version}",
    ])

    archives = sorted(downloads.glob("*.tar.gz"))
    if not archives:
        raise SystemExit(f"no source archive downloaded for {name}")

    extract = temporary / f"extract-{name}"
    extract.mkdir()
    with tarfile.open(archives[0]) as archive:
        archive.extractall(extract, filter="data")

    package_dir = next(
        p for p in extract.rglob(name) if p.is_dir() and (p / "__init__.py").exists()
    )
    destination = TARGET / name
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    kept = []
    for source in package_dir.iterdir():
        if source.is_file() and source.suffix in (".py", ".txt", ".typed"):
            shutil.copy2(source, destination / source.name)
            kept.append(source.name)
    print(f"{name}: kept {sorted(kept)} (C extension omitted)")


def main() -> int:
    if not TARGET.parent.exists():
        print(
            "Python framework missing; run Scripts/fetch_python_framework.sh first.",
            file=sys.stderr,
        )
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)

        # Phase 1: the package pip cannot resolve for a generic platform.
        for name, version in SOURCE_ONLY_PACKAGES:
            install_source_only(temporary, name, version)

        # Phase 2: everything else, with dependency resolution disabled.
        stage = temporary / "pure"
        stage.mkdir()
        run([
            sys.executable, "-m", "pip", "install",
            "--target", str(stage),
            "--platform", "any",
            "--python-version", PY_MINOR,
            "--only-binary=:all:",
            "--no-compile",
            "--no-deps",
            "--upgrade",
            *PURE_PACKAGES,
        ])
        merge(stage)

    extensions = [
        str(p.relative_to(TARGET))
        for pattern in ("*.so", "*.pyd", "*.dylib")
        for p in TARGET.rglob(pattern)
    ]
    if extensions:
        print("compiled extensions found (will not load on device):", file=sys.stderr)
        for name in extensions[:20]:
            print("  ", name, file=sys.stderr)
        return 1

    # Sanity check: every package must actually import with no extension present.
    sys.path.insert(0, str(TARGET))
    failures = []
    for module in ("flask", "jinja2", "markupsafe", "werkzeug", "waitress",
                   "httpx", "httpcore", "h2", "qrcode"):
        try:
            __import__(module)
        except Exception as error:  # noqa: BLE001 - report and keep going
            failures.append(f"{module}: {error}")
    if failures:
        print("import check failed:", file=sys.stderr)
        for line in failures:
            print("  ", line, file=sys.stderr)
        return 1

    print(f"import check ok: 9 modules load from pure Python")
    installed = sorted(p.name for p in TARGET.iterdir() if p.is_dir())
    print(f"installed {len(installed)} distributions into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
