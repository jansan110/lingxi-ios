"""RSA private-key support for the ``cryptography`` shim.

Production code only ever *encrypts* with a public key (``coc_wheel``,
``net_age_surprise_protocol``). The upstream test-suite, however, generates a key
pair to act as the server and decrypts what the client sent. Implementing the
same operations keeps the iOS build able to run that suite unchanged, which is
the only way to prove the public-key path is correct against a counterpart
rather than against itself.

Scope is deliberately exact: PKCS#1 v1.5 encrypt/decrypt and key generation.
No signing, no OAEP -- nothing in this project reaches for them.
"""

from __future__ import annotations

import os
import secrets


# --------------------------------------------------------------------------
# Miller-Rabin, deterministic for the sizes used here
# --------------------------------------------------------------------------
_SMALL_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53,
                 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113)


def _is_probable_prime(value: int, rounds: int = 40) -> bool:
    if value < 2:
        return False
    for small in _SMALL_PRIMES:
        if value == small:
            return True
        if value % small == 0:
            return False

    # write value-1 as d * 2^s
    exponent = value - 1
    shift = 0
    while exponent % 2 == 0:
        exponent //= 2
        shift += 1

    for _ in range(rounds):
        base = secrets.randbelow(value - 3) + 2
        witness = pow(base, exponent, value)
        if witness in (1, value - 1):
            continue
        for _ in range(shift - 1):
            witness = pow(witness, 2, value)
            if witness == value - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if candidate % 65537 == 0:
            continue
        if _is_probable_prime(candidate):
            return candidate


def _mod_inverse(value: int, modulus: int) -> int:
    # extended Euclid; raises like Python's pow(..., -1, n) when not coprime
    return pow(value, -1, modulus)


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
class RSAPrivateNumbers:
    """The raw CRT parameters, mirroring the ``cryptography`` container."""

    def __init__(self, p, q, d, dmp1, dmq1, iqmp, public_numbers):
        self.p, self.q, self.d = int(p), int(q), int(d)
        self.dmp1, self.dmq1 = int(dmp1), int(dmq1)
        self.iqmp = int(iqmp)
        self.public_numbers = public_numbers

    def private_key(self):
        return RSAPrivateKey(self)


class RSAPrivateKey:
    def __init__(self, numbers: RSAPrivateNumbers):
        from .crypto import RSAPublicKey  # single definition lives in crypto.py

        self._numbers = numbers
        self._public = RSAPublicKey(numbers.public_numbers.e, numbers.public_numbers.n)

    def public_key(self) -> "RSAPublicKey":
        return self._public

    @property
    def key_size(self) -> int:
        return self._public.n.bit_length()

    def decrypt(self, ciphertext: bytes, padding) -> bytes:
        from . import crypto  # local import to avoid a cycle

        if not isinstance(padding, crypto.PKCS1v15):
            raise ValueError("only PKCS1v15 decryption is implemented")
        numbers = self._numbers
        size = (self._public.n.bit_length() + 7) // 8
        if len(ciphertext) != size:
            raise ValueError("Ciphertext length must be equal to key size.")
        block = int.from_bytes(ciphertext, "big")
        if block >= self._public.n:
            raise ValueError("Ciphertext too large.")

        # CRT path: faster than a plain modexp and equivalent in value.
        p, q = numbers.p, numbers.q
        m1 = pow(block % p, numbers.dmp1, p)
        m2 = pow(block % q, numbers.dmq1, q)
        h = (numbers.iqmp * (m1 - m2)) % p
        message = m2 + h * q
        raw = message.to_bytes(size, "big")

        if raw[:2] != b"\x00\x02":
            raise ValueError("Decryption failed: invalid PKCS1 v1.5 padding")
        separator = raw.find(b"\x00", 2)
        if separator < 0 or separator < 10:
            raise ValueError("Decryption failed: invalid PKCS1 v1.5 padding")
        return raw[separator + 1:]


def generate_private_key(public_exponent: int = 65537, key_size: int = 2048) -> RSAPrivateKey:
    """``cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key``."""
    if public_exponent < 3 or public_exponent % 2 == 0:
        raise ValueError("public_exponent must be odd and >= 3")
    if key_size < 512 or key_size % 2:
        raise ValueError("key_size must be an even number >= 512")

    e = int(public_exponent)
    while True:
        p = _random_prime(key_size // 2)
        q = _random_prime(key_size // 2)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != key_size:
            continue
        phi = (p - 1) * (q - 1)
        try:
            d = _mod_inverse(e, phi)
        except ValueError:
            continue
        numbers = RSAPrivateNumbers(
            p=p, q=q, d=d,
            dmp1=d % (p - 1), dmq1=d % (q - 1),
            iqmp=pow(q, -1, p),
            public_numbers=_public_numbers(e, n),
        )
        return numbers.private_key()


def _public_numbers(e: int, n: int):
    from . import crypto

    return crypto.RSAPublicNumbers(e, n)
