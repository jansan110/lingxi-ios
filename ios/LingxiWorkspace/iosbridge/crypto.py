"""A byte-exact pure-Python stand-in for the ``cryptography`` API surface used here.

Only the primitives this workbench actually calls are implemented:

    cryptography.hazmat.primitives.ciphers          Cipher, algorithms, modes
    cryptography.hazmat.primitives.ciphers.aead     AESGCM
    cryptography.hazmat.primitives.padding          PKCS7
    cryptography.hazmat.primitives.asymmetric.rsa   RSAPublicNumbers
    cryptography.hazmat.primitives.asymmetric.padding  PKCS1v15
    cryptography.hazmat.primitives.serialization    load_der_public_key
    cryptography.hazmat.decrepit.ciphers.algorithms TripleDES
    cryptography.exceptions                         InvalidTag

iOS ships no PyPI wheel for ``cryptography`` (its Rust/OpenSSL build does not
target the platform), so this module supplies the same calls. Every mode is
implemented from the published algorithm definition rather than approximated,
and ``tests/test_crypto_parity.py`` checks the output byte-for-byte against the
real library.
"""

from __future__ import annotations

import base64
import struct


class InvalidTag(Exception):
    """Mirrors ``cryptography.exceptions.InvalidTag``."""


# ==========================================================================
# AES (FIPS-197)
# ==========================================================================
def _build_sbox():
    sbox = [0] * 256
    p = 1
    q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        sbox[p] = q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4)
        sbox[p] &= 0xFF
        sbox[p] ^= 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _build_sbox()
INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i

RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value = (value ^ 0x1B) & 0xFF
    return value


def _gmul(a: int, b: int) -> int:
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = _xtime(a)
    return result


def _expand_key(key: bytes) -> list[list[int]]:
    nk = len(key) // 4
    rounds = {4: 10, 6: 12, 8: 14}[nk]
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (rounds + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


def _add_round_key(state: list[int], round_key: list[int]):
    for i in range(16):
        state[i] ^= round_key[i]


def _sub_bytes(state, box):
    for i in range(16):
        state[i] = box[state[i]]


def _shift_rows(state: list[int]):
    state[1], state[5], state[9], state[13] = state[5], state[9], state[13], state[1]
    state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
    state[3], state[7], state[11], state[15] = state[15], state[3], state[7], state[11]


def _inv_shift_rows(state: list[int]):
    state[1], state[5], state[9], state[13] = state[13], state[1], state[5], state[9]
    state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
    state[3], state[7], state[11], state[15] = state[7], state[11], state[15], state[3]


def _mix_columns(state: list[int]):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
        state[i] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
        state[i + 1] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
        state[i + 2] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
        state[i + 3] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)


def _inv_mix_columns(state: list[int]):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
        state[i] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
        state[i + 1] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
        state[i + 2] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
        state[i + 3] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)


class _AesCore:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 128, 192 or 256 bits")
        self.rounds = {16: 10, 24: 12, 32: 14}[len(key)]
        self.round_keys = _expand_key(key)

    def encrypt_block(self, block: bytes) -> bytes:
        state = list(block)
        _add_round_key(state, self.round_keys[0])
        for r in range(1, self.rounds):
            _sub_bytes(state, SBOX)
            _shift_rows(state)
            _mix_columns(state)
            _add_round_key(state, self.round_keys[r])
        _sub_bytes(state, SBOX)
        _shift_rows(state)
        _add_round_key(state, self.round_keys[self.rounds])
        return bytes(state)

    def decrypt_block(self, block: bytes) -> bytes:
        state = list(block)
        _add_round_key(state, self.round_keys[self.rounds])
        for r in range(self.rounds - 1, 0, -1):
            _inv_shift_rows(state)
            _sub_bytes(state, INV_SBOX)
            _add_round_key(state, self.round_keys[r])
            _inv_mix_columns(state)
        _inv_shift_rows(state)
        _sub_bytes(state, INV_SBOX)
        _add_round_key(state, self.round_keys[0])
        return bytes(state)


# ==========================================================================
# DES / TripleDES (FIPS 46-3)
# ==========================================================================
_IP = [58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
       62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
       57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
       61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7]
_FP = [40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
       38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
       36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
       34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25]
_E = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
      12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
      24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]
_P = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
      2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25]
_PC1 = [57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
        10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
        63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
        14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4]
_PC2 = [14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
        23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
        41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
        44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

_SBOXES = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _permute(value: int, table, nbits: int) -> int:
    out = 0
    for position in table:
        out = (out << 1) | ((value >> (nbits - position)) & 1)
    return out


_ALL_SHIFTS = [sum(_SHIFTS[:i]) for i in range(17)]


class _DesCore:
    def __init__(self, key: bytes, decrypt: bool = False):
        if len(key) != 8:
            raise ValueError("DES key must be 64 bits")
        value = int.from_bytes(key, "big")
        permuted = _permute(value, _PC1, 64)
        left = permuted >> 28
        right = permuted & 0x0FFFFFFF
        self.subkeys = []
        for i in range(16):
            shift = _SHIFTS[i]
            left = ((left << shift) | (left >> (28 - shift))) & 0x0FFFFFFF
            right = ((right << shift) | (right >> (28 - shift))) & 0x0FFFFFFF
            self.subkeys.append(_permute((left << 28) | right, _PC2, 56))
        if decrypt:
            self.subkeys.reverse()

    def _feistel(self, half: int, subkey: int) -> int:
        expanded = _permute(half, _E, 32) ^ subkey
        out = 0
        for i in range(8):
            chunk = (expanded >> (42 - 6 * i)) & 0x3F
            row = ((chunk & 0x20) >> 4) | (chunk & 1)
            column = (chunk >> 1) & 0x0F
            out = (out << 4) | _SBOXES[i][row * 16 + column]
        return _permute(out, _P, 32)

    def crypt_block(self, block: bytes) -> bytes:
        value = _permute(int.from_bytes(block, "big"), _IP, 64)
        left = value >> 32
        right = value & 0xFFFFFFFF
        for subkey in self.subkeys:
            left, right = right, left ^ self._feistel(right, subkey)
        return _permute((right << 32) | left, _FP, 64).to_bytes(8, "big")


# ==========================================================================
# Cipher / algorithms / modes
# ==========================================================================
class _Algorithm:
    block_size = 16
    key_sizes = (128,)

    def __init__(self, key: bytes):
        self.key = bytes(key)


class AES(_Algorithm):
    block_size = 16
    key_sizes = (128, 192, 256)

    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("Invalid key size ({0}) for AES".format(len(key) * 8))
        super().__init__(key)
        self.core = _AesCore(key)


class TripleDES(_Algorithm):
    block_size = 8
    key_sizes = (64, 128, 192)

    def __init__(self, key: bytes):
        if len(key) not in (8, 16, 24):
            raise ValueError("Invalid key size ({0}) for TripleDES".format(len(key) * 8))
        super().__init__(key)
        # cryptography expands 8-byte keys to K1=K2=K3 and 16-byte keys to
        # K1,K2,K1, exactly the same way the reference library does.
        if len(key) == 8:
            key = key * 3
        elif len(key) == 16:
            key = key + key[:8]
        self.key3 = key


class ECB:
    name = "ECB"


class CBC:
    name = "CBC"

    def __init__(self, initialization_vector: bytes):
        self.initialization_vector = bytes(initialization_vector)


def _triple_des_subkeys(algo: TripleDES):
    """(encrypt cores, decrypt cores) built once per algorithm instance."""
    cached = getattr(algo, "_subkeys", None)
    if cached is None:
        key = algo.key3
        cached = (
            (_DesCore(key[0:8], False), _DesCore(key[8:16], True), _DesCore(key[16:24], False)),
            (_DesCore(key[0:8], True), _DesCore(key[8:16], False), _DesCore(key[16:24], True)),
        )
        algo._subkeys = cached
    return cached


def _encrypt_blocks(algo, data: bytes) -> bytes:
    out = bytearray()
    if isinstance(algo, AES):
        for i in range(0, len(data), 16):
            out += algo.core.encrypt_block(data[i:i + 16])
        return bytes(out)
    first, second, third = _triple_des_subkeys(algo)[0]
    for i in range(0, len(data), 8):
        block = data[i:i + 8]
        out += third.crypt_block(second.crypt_block(first.crypt_block(block)))
    return bytes(out)


def _decrypt_blocks(algo, data: bytes) -> bytes:
    out = bytearray()
    if isinstance(algo, AES):
        for i in range(0, len(data), 16):
            out += algo.core.decrypt_block(data[i:i + 16])
        return bytes(out)
    first, second, third = _triple_des_subkeys(algo)[1]
    for i in range(0, len(data), 8):
        block = data[i:i + 8]
        out += first.crypt_block(second.crypt_block(third.crypt_block(block)))
    return bytes(out)


class _CipherContext:
    def __init__(self, algo, mode, encrypting: bool):
        self.algo = algo
        self.mode = mode
        self.encrypting = encrypting
        self.buffer = b""
        self.iv = getattr(mode, "initialization_vector", None)

    def update(self, data: bytes) -> bytes:
        data = self.buffer + bytes(data)
        block = self.algo.block_size
        usable = len(data) - (len(data) % block)
        self.buffer = data[usable:]
        data = data[:usable]
        if not data:
            return b""
        if isinstance(self.mode, ECB):
            return _encrypt_blocks(self.algo, data) if self.encrypting else _decrypt_blocks(self.algo, data)
        out = bytearray()
        iv = self.iv
        if self.encrypting:
            for i in range(0, len(data), block):
                block_data = bytes(a ^ b for a, b in zip(data[i:i + block], iv))
                iv = _encrypt_blocks(self.algo, block_data)
                out += iv
        else:
            for i in range(0, len(data), block):
                chunk = data[i:i + block]
                plain = _decrypt_blocks(self.algo, chunk)
                out += bytes(a ^ b for a, b in zip(plain, iv))
                iv = chunk
        self.iv = iv
        return bytes(out)

    def finalize(self) -> bytes:
        if self.buffer:
            raise ValueError("The length of the provided data is not a multiple of the block length.")
        if isinstance(self.mode, ECB) or self.encrypting:
            return b""
        return b""


class Cipher:
    def __init__(self, algorithm, mode):
        if isinstance(mode, CBC) and len(mode.initialization_vector) != algorithm.block_size:
            raise ValueError("Invalid IV length")
        self.algorithm = algorithm
        self.mode = mode

    def encryptor(self):
        return _CipherContext(self.algorithm, self.mode, True)

    def decryptor(self):
        return _CipherContext(self.algorithm, self.mode, False)


class algorithms:
    AES = AES
    TripleDES = TripleDES


class modes:
    ECB = ECB
    CBC = CBC


# ==========================================================================
# PKCS7 padding
# ==========================================================================
class _PKCS7Padder:
    def __init__(self, block_size: int):
        self.block_size = block_size
        self.buffer = b""

    def update(self, data: bytes) -> bytes:
        data = self.buffer + bytes(data)
        usable = len(data) - (len(data) % self.block_size)
        self.buffer = data[usable:]
        return data[:usable]

    def finalize(self) -> bytes:
        pad = self.block_size - len(self.buffer)
        return self.buffer + bytes([pad]) * pad


class _PKCS7Unpadder:
    def __init__(self, block_size: int):
        self.block_size = block_size
        self.buffer = b""

    def update(self, data: bytes) -> bytes:
        data = self.buffer + bytes(data)
        if len(data) <= self.block_size:
            self.buffer = data
            return b""
        cut = len(data) - (len(data) % self.block_size)
        if cut == len(data):
            cut -= self.block_size
        self.buffer = data[cut:]
        return data[:cut]

    def finalize(self) -> bytes:
        if not self.buffer:
            raise ValueError("Invalid padding bytes.")
        pad = self.buffer[-1]
        if pad < 1 or pad > self.block_size or self.buffer[-pad:] != bytes([pad]) * pad:
            raise ValueError("Invalid padding bytes.")
        return self.buffer[:-pad]


class PKCS7:
    def __init__(self, block_size: int):
        if block_size not in (8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128):
            raise ValueError("block_size must be a multiple of 8 and between 8 and 128")
        self.block_size = block_size // 8

    def padder(self):
        return _PKCS7Padder(self.block_size)

    def unpadder(self):
        return _PKCS7Unpadder(self.block_size)


# ==========================================================================
# AES-GCM (NIST SP 800-38D)
# ==========================================================================
_R = 0xE1 << 120


def _gf_mult(x: int, y: int) -> int:
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _R
        else:
            v >>= 1
    return z


def _ghash(h: int, aad: bytes, ciphertext: bytes) -> int:
    def blocks(data):
        for i in range(0, len(data), 16):
            yield int.from_bytes(data[i:i + 16].ljust(16, b"\x00"), "big")
    y = 0
    for block in blocks(aad):
        y = _gf_mult(y ^ block, h)
    for block in blocks(ciphertext):
        y = _gf_mult(y ^ block, h)
    length = ((len(aad) * 8) << 64) | (len(ciphertext) * 8)
    return _gf_mult(y ^ length, h)


def _ctr(core: _AesCore, nonce: bytes, data: bytes) -> bytes:
    # SP 800-38D: J0 = nonce || 0x00000001 for a 96-bit IV, and the payload is
    # encrypted starting at inc32(J0) == 0x00000002. J0 itself only masks the tag.
    out = bytearray()
    for index in range(0, len(data), 16):
        block_index = index // 16 + 2
        counter = nonce + (block_index & 0xFFFFFFFF).to_bytes(4, "big")
        stream = core.encrypt_block(counter)
        out += bytes(a ^ b for a, b in zip(data[index:index + 16], stream))
    return bytes(out)


class AESGCM:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AESGCM key must be 128, 192, or 256 bits.")
        self.key = key
        self.core = _AesCore(key)
        self.h = int.from_bytes(self.core.encrypt_block(b"\x00" * 16), "big")

    def encrypt(self, nonce: bytes, data: bytes, associated_data=None) -> bytes:
        aad = bytes(associated_data or b"")
        ciphertext = _ctr(self.core, nonce, bytes(data))
        tag = _ghash(self.h, aad, ciphertext) ^ int.from_bytes(
            self.core.encrypt_block(nonce + b"\x00\x00\x00\x01"), "big")
        return ciphertext + tag.to_bytes(16, "big")

    def decrypt(self, nonce: bytes, data: bytes, associated_data=None) -> bytes:
        aad = bytes(associated_data or b"")
        data = bytes(data)
        if len(data) < 16:
            raise InvalidTag
        ciphertext, tag = data[:-16], data[-16:]
        expected = _ghash(self.h, aad, ciphertext) ^ int.from_bytes(
            self.core.encrypt_block(nonce + b"\x00\x00\x00\x01"), "big")
        if not _constant_time_equal(tag, expected.to_bytes(16, "big")):
            raise InvalidTag
        return _ctr(self.core, nonce, ciphertext)


def _constant_time_equal(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


# ==========================================================================
# RSA PKCS#1 v1.5 encryption
# ==========================================================================
class RSAPublicNumbers:
    def __init__(self, e: int, n: int):
        self.e = int(e)
        self.n = int(n)

    def public_key(self):
        return RSAPublicKey(self.e, self.n)


class RSAPublicKey:
    def __init__(self, e: int, n: int):
        self.e = e
        self.n = n
        self.key_size = n.bit_length()

    def public_bytes(self, encoding=None, format=None):
        """Serialise as SubjectPublicKeyInfo, matching ``cryptography``.

        Defaults to DER/PEM when the caller passes nothing, which is what the
        upstream tests do.
        """
        from . import der

        written = der.encode_public_key(self.e, self.n)
        name = getattr(encoding, "name", str(encoding))
        if name in ("PEM", "Encoding.PEM"):
            return der.encode_pem(written)
        return written

    def public_numbers(self):
        return RSAPublicNumbers(self.e, self.n)

    def encrypt(self, plaintext: bytes, padding) -> bytes:
        if not isinstance(padding, PKCS1v15):
            raise ValueError("only PKCS1v15 is supported")
        k = (self.key_size + 7) // 8
        message = bytes(plaintext)
        if len(message) > k - 11:
            raise ValueError("Data too long for key size.")
        ps = bytearray()
        while len(ps) < k - len(message) - 3:
            byte = b"\x00"
            while byte == b"\x00":
                byte = _urandom(1)
            ps += byte
        block = b"\x00\x02" + bytes(ps) + b"\x00" + message
        value = pow(int.from_bytes(block, "big"), self.e, self.n)
        return value.to_bytes(k, "big")


def _urandom(n: int) -> bytes:
    import os

    return os.urandom(n)


class PKCS1v15:
    name = "PKCS1v15"


# ==========================================================================
# DER parsing for the Alipay public key
# ==========================================================================
def _der_read(data: bytes, offset: int):
    tag = data[offset]
    offset += 1
    length = data[offset]
    offset += 1
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    return tag, data[offset:offset + length], offset + length


def _der_int(blob: bytes) -> int:
    return int.from_bytes(blob, "big")


class serialization:
    @staticmethod
    def load_der_public_key(data: bytes):
        tag, body, _ = _der_read(bytes(data), 0)
        if tag != 0x30:
            raise ValueError("Invalid DER public key.")
        tag, first, offset = _der_read(body, 0)
        if tag == 0x30:
            # SubjectPublicKeyInfo: AlgorithmIdentifier then BIT STRING
            tag, bits, _ = _der_read(body, offset)
            if tag != 0x03:
                raise ValueError("Invalid DER public key.")
            inner = bits[1:]
            tag, key_body, _ = _der_read(inner, 0)
            if tag != 0x30:
                raise ValueError("Invalid DER public key.")
            tag, modulus, offset = _der_read(key_body, 0)
            tag_e, exponent, _ = _der_read(key_body, offset)
            return RSAPublicNumbers(_der_int(exponent), _der_int(modulus)).public_key()
        # PKCS#1 RSAPublicKey
        tag_n, modulus, offset = _der_read(body, offset)
        tag_e, exponent, _ = _der_read(body, offset)
        return RSAPublicNumbers(_der_int(exponent), _der_int(modulus)).public_key()

    @staticmethod
    def load_pem_public_key(data: bytes):
        text = bytes(data).decode("ascii", "strict")
        begin = "-----BEGIN PUBLIC KEY-----"
        end = "-----END PUBLIC KEY-----"
        if begin not in text or end not in text:
            raise ValueError("Not a PEM encoded SubjectPublicKeyInfo.")
        body = text.split(begin, 1)[1].split(end, 1)[0]
        der = base64.b64decode("".join(body.split()), validate=True)
        return serialization.load_der_public_key(der)
