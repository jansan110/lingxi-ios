"""DER/PEM encoding for the ``cryptography`` shim.

``coc_wheel`` and ``net_age_surprise_protocol`` only ever *parse* a public key,
but the upstream tests re-serialise a generated key to check the pinned
fingerprint. Providing the encoder keeps that check meaningful instead of
skipped: it writes the same SubjectPublicKeyInfo structure that
``iosbridge.crypto.serialization.load_der_public_key`` reads back.

Only RSAPublicKey is encoded, which is the only key type this project has.
"""

from __future__ import annotations

import base64


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _tagged(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _length(len(body)) + body


def _integer(value: int) -> bytes:
    if value == 0:
        return _tagged(0x02, b"\x00")
    encoded = value.to_bytes((value.bit_length() + 8) // 8, "big")
    if encoded[0] & 0x80:  # keep the sign bit clear
        encoded = b"\x00" + encoded
    return _tagged(0x02, encoded)


# AlgorithmIdentifier for rsaEncryption, with the NULL parameters OpenSSL emits.
_RSA_ALGORITHM = _tagged(0x30, _tagged(0x06, bytes.fromhex("2a864886f70d010101")) + _tagged(0x05, b""))


def encode_public_key(e: int, n: int) -> bytes:
    """SubjectPublicKeyInfo DER for an RSA public key."""
    key = _tagged(0x30, _integer(n) + _integer(e))
    return _tagged(0x30, _RSA_ALGORITHM + _tagged(0x03, b"\x00" + key))


def encode_pem(der: bytes, label: str = "PUBLIC KEY") -> bytes:
    body = base64.encodebytes(der).replace(b"\n", b"")
    lines = [body[index:index + 64] for index in range(0, len(body), 64)]
    text = "-----BEGIN {0}-----\n{1}\n-----END {0}-----\n".format(
        label, b"\n".join(lines).decode("ascii"))
    return text.encode("ascii")
