"""Chaquopy-compatible ``java`` module for the iOS build.

Android reaches its five native classes through Chaquopy's ``java`` package.
This module provides the same import surface (``jclass``, ``jarray``,
``jbyte`` …) so every business module that talks to the platform layer keeps
working unchanged. The implementations live in ``iosbridge/``.
"""

from iosbridge.bridge import (  # noqa: F401  (re-exported import surface)
    CredentialVault,
    CvException,
    JavaIOException,
    MemberBenefitsV8,
    NativeHttp,
    NativeHttpResult,
    OpenCVLoader,
    jclass,
)
from iosbridge.javatypes import jarray, jbyte, jchar, jdouble, jfloat, jint, jlong, jshort  # noqa: F401

__all__ = [
    "jclass", "jarray", "jbyte", "jchar", "jshort", "jint", "jlong", "jfloat", "jdouble",
    "CredentialVault", "NativeHttp", "NativeHttpResult", "MemberBenefitsV8",
    "OpenCVLoader", "JavaIOException", "CvException",
]
