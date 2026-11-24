"""Installs ``iosbridge.crypto`` under the ``cryptography`` package names.

iOS has no PyPI wheel for ``cryptography``, so the business modules that
``from cryptography...`` are pointed at the pure-Python implementation instead.
The shim registers itself in ``sys.modules`` on import; the app imports it
before any business module (see ``iosbridge.bootstrap``), so every later
``cryptography`` import resolves to the local implementation.

A real ``cryptography`` installation always wins, which keeps the host test run
able to compare both implementations side by side.
"""

from __future__ import annotations

import sys
import types

from iosbridge import crypto as _crypto


def _register(name: str, attributes: dict | None = None) -> types.ModuleType:
    """Create ``name`` in ``sys.modules`` and attach it to its parent package."""
    module = types.ModuleType(name)
    if attributes:
        module.__dict__.update(attributes)
    sys.modules[name] = module
    parent, _, leaf = name.rpartition(".")
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], leaf, module)
    return module


def install() -> None:
    """Register every ``cryptography`` path the business layer imports."""
    existing = sys.modules.get("cryptography")
    if existing is not None and not getattr(existing, "__iosbridge__", False):
        # A real installation is present; leave it alone.
        return

    root = _register("cryptography")
    root.__iosbridge__ = True
    exceptions = _register("cryptography.exceptions", {
        "InvalidTag": _crypto.InvalidTag,
        "InvalidSignature": Exception,
    })
    root.exceptions = exceptions

    hazmat = _register("cryptography.hazmat")
    root.hazmat = hazmat
    primitives = _register("cryptography.hazmat.primitives")
    hazmat.primitives = primitives

    primitives.padding = _register("cryptography.hazmat.primitives.padding", {"PKCS7": _crypto.PKCS7})

    ciphers = _register("cryptography.hazmat.primitives.ciphers", {
        "Cipher": _crypto.Cipher,
        "algorithms": _crypto.algorithms,
        "modes": _crypto.modes,
    })
    primitives.ciphers = ciphers

    ciphers.aead = _register("cryptography.hazmat.primitives.ciphers.aead", {"AESGCM": _crypto.AESGCM})

    asymmetric = _register("cryptography.hazmat.primitives.asymmetric")
    primitives.asymmetric = asymmetric
    from iosbridge import rsa_keys

    asymmetric.rsa = _register("cryptography.hazmat.primitives.asymmetric.rsa", {
        "RSAPublicNumbers": _crypto.RSAPublicNumbers,
        "RSAPublicKey": _crypto.RSAPublicKey,
        "RSAPrivateNumbers": rsa_keys.RSAPrivateNumbers,
        "RSAPrivateKey": rsa_keys.RSAPrivateKey,
        "generate_private_key": rsa_keys.generate_private_key,
    })
    asymmetric.padding = _register("cryptography.hazmat.primitives.asymmetric.padding", {
        "PKCS1v15": _crypto.PKCS1v15,
    })

    class _Enum:
        """A tiny stand-in for ``cryptography``'s enum members."""

        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return self.name

    primitives.serialization = _register("cryptography.hazmat.primitives.serialization", {
        "load_der_public_key": _crypto.serialization.load_der_public_key,
        "load_pem_public_key": _crypto.serialization.load_pem_public_key,
        "Encoding": type("Encoding", (), {"DER": _Enum("DER"), "PEM": _Enum("PEM")}),
        "PublicFormat": type("PublicFormat", (), {
            "SubjectPublicKeyInfo": _Enum("SubjectPublicKeyInfo"),
            "PKCS1": _Enum("PKCS1"),
        }),
    })

    decrepit = _register("cryptography.hazmat.decrepit")
    hazmat.decrepit = decrepit
    decrepit.ciphers = _register("cryptography.hazmat.decrepit.ciphers")
    decrepit.ciphers.algorithms = _register("cryptography.hazmat.decrepit.ciphers.algorithms", {
        "TripleDES": _crypto.TripleDES,
    })


install()
