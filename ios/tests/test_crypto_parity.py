"""Byte-for-byte parity checks against the real ``cryptography`` package.

Every primitive in ``iosbridge.crypto`` is verified against the reference
implementation on random and fixed vectors. Run this on a host that has
``cryptography`` installed; the iOS app never runs it.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'LingxiWorkspace'))

from iosbridge import crypto as mine  # noqa: E402

try:
    from cryptography.hazmat.primitives.ciphers import Cipher as RefCipher, algorithms as ref_algo, modes as ref_modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as RefAESGCM
    from cryptography.hazmat.primitives import padding as ref_padding
    from cryptography.hazmat.primitives.asymmetric import rsa as ref_rsa, padding as ref_pad
    from cryptography.hazmat.primitives import serialization as ref_serialization
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES as RefTripleDES
    HAVE_REF = True
except Exception:  # noqa: BLE001
    HAVE_REF = False


@unittest.skipUnless(HAVE_REF, "reference cryptography not installed")
class CryptoParity(unittest.TestCase):
    # -- AES ECB / CBC --------------------------------------------------
    def test_aes_ecb_matches_reference(self):
        for size in (16, 24, 32):
            key = os.urandom(size)
            data = os.urandom(16 * 5)
            ref = RefCipher(ref_algo.AES(key), ref_modes.ECB()).encryptor()
            expected = ref.update(data) + ref.finalize()
            enc = mine.Cipher(mine.algorithms.AES(key), mine.modes.ECB()).encryptor()
            self.assertEqual(enc.update(data) + enc.finalize(), expected)
            dec = mine.Cipher(mine.algorithms.AES(key), mine.modes.ECB()).decryptor()
            self.assertEqual(dec.update(expected) + dec.finalize(), data)

    def test_aes_cbc_matches_reference(self):
        for size in (16, 24, 32):
            key, iv = os.urandom(size), os.urandom(16)
            data = os.urandom(16 * 4)
            ref = RefCipher(ref_algo.AES(key), ref_modes.CBC(iv)).encryptor()
            expected = ref.update(data) + ref.finalize()
            enc = mine.Cipher(mine.algorithms.AES(key), mine.modes.CBC(iv)).encryptor()
            self.assertEqual(enc.update(data) + enc.finalize(), expected)
            dec = mine.Cipher(mine.algorithms.AES(key), mine.modes.CBC(iv)).decryptor()
            self.assertEqual(dec.update(expected) + dec.finalize(), data)

    def test_aes_streaming_chunking_is_equivalent(self):
        key, iv = os.urandom(32), os.urandom(16)
        data = os.urandom(48)
        ref = RefCipher(ref_algo.AES(key), ref_modes.CBC(iv)).encryptor()
        expected = b"".join(ref.update(data[i:i + 7]) for i in range(0, len(data), 7)) + ref.finalize()
        enc = mine.Cipher(mine.algorithms.AES(key), mine.modes.CBC(iv)).encryptor()
        actual = b"".join(enc.update(data[i:i + 7]) for i in range(0, len(data), 7)) + enc.finalize()
        self.assertEqual(actual, expected)

    # -- TripleDES ------------------------------------------------------
    def test_tripledes_cbc_and_ecb_match_reference(self):
        for size in (8, 16, 24):
            key = os.urandom(size)
            iv = os.urandom(8)
            data = os.urandom(8 * 4)
            for ref_mode, my_mode in ((ref_modes.CBC(iv), mine.modes.CBC(iv)),
                                      (ref_modes.ECB(), mine.modes.ECB())):
                ref = RefCipher(RefTripleDES(key), ref_mode).encryptor()
                expected = ref.update(data) + ref.finalize()
                enc = mine.Cipher(mine.algorithms.TripleDES(key), my_mode).encryptor()
                self.assertEqual(enc.update(data) + enc.finalize(), expected, size)
                ref_dec = RefCipher(RefTripleDES(key), ref_mode).decryptor()
                expected_plain = ref_dec.update(expected) + ref_dec.finalize()
                dec = mine.Cipher(mine.algorithms.TripleDES(key), my_mode).decryptor()
                self.assertEqual(dec.update(expected) + dec.finalize(), expected_plain)

    # -- PKCS7 ----------------------------------------------------------
    def test_pkcs7_round_trips_and_matches_reference(self):
        for block_bits in (64, 128):
            for length in (0, 1, 7, 8, 15, 16, 33):
                data = os.urandom(length)
                ref = ref_padding.PKCS7(block_bits).padder()
                expected = ref.update(data) + ref.finalize()
                pad = mine.PKCS7(block_bits).padder()
                self.assertEqual(pad.update(data) + pad.finalize(), expected, (block_bits, length))
                unpad = mine.PKCS7(block_bits).unpadder()
                self.assertEqual(unpad.update(expected) + unpad.finalize(), data)

    # -- AES-GCM --------------------------------------------------------
    def test_aesgcm_matches_reference(self):
        for size in (16, 24, 32):
            key = os.urandom(size)
            nonce = os.urandom(12)
            for aad, length in ((None, 0), (None, 17), (b"hdr", 40)):
                data = os.urandom(length)
                expected = RefAESGCM(key).encrypt(nonce, data, aad)
                self.assertEqual(mine.AESGCM(key).encrypt(nonce, data, aad), expected)
                self.assertEqual(mine.AESGCM(key).decrypt(nonce, expected, aad), data)

    def test_aesgcm_rejects_tampered_tag(self):
        key, nonce = os.urandom(32), os.urandom(12)
        blob = bytearray(mine.AESGCM(key).encrypt(nonce, b"payload bytes", None))
        blob[-1] ^= 0x01
        with self.assertRaises(mine.InvalidTag):
            mine.AESGCM(key).decrypt(nonce, bytes(blob), None)

    # -- RSA ------------------------------------------------------------
    def test_rsa_pkcs1v15_matches_reference_for_same_plaintext(self):
        # PKCS#1 v1.5 padding is randomised; decrypt to compare, not bytes.
        private = ref_rsa.generate_private_key(public_exponent=65537, key_size=1024)
        numbers = private.public_key().public_numbers()
        mine_key = mine.RSAPublicNumbers(numbers.e, numbers.n).public_key()
        payload = b"leadeon138000000001790000000"
        blob = mine_key.encrypt(payload, mine.PKCS1v15())
        self.assertEqual(len(blob), private.key_size // 8)
        self.assertEqual(private.decrypt(blob, ref_pad.PKCS1v15()), payload)
        # And a reference ciphertext decrypts through the same modulus.
        ref_blob = private.public_key().encrypt(payload, ref_pad.PKCS1v15())
        self.assertEqual(private.decrypt(ref_blob, ref_pad.PKCS1v15()), payload)

    def test_der_public_key_parsing(self):
        private = ref_rsa.generate_private_key(public_exponent=65537, key_size=1024)
        der = private.public_key().public_bytes(
            ref_serialization.Encoding.DER,
            ref_serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        mine_key = mine.serialization.load_der_public_key(der)
        numbers = private.public_key().public_numbers()
        self.assertEqual((mine_key.e, mine_key.n), (numbers.e, numbers.n))

    def test_star_day_crypto_known_vector(self):
        # The fixed key used by star_day_crypto.py
        from cryptography.hazmat.primitives import padding as p
        padder = p.PKCS7(128).padder()
        padded = padder.update(b"fixture") + padder.finalize()
        ref = RefCipher(ref_algo.AES(b"WX812FB36C5901D3"), ref_modes.ECB()).encryptor()
        expected = base64.b64encode(ref.update(padded) + ref.finalize()).decode()

        my_pad = mine.PKCS7(128).padder()
        my_padded = my_pad.update(b"fixture") + my_pad.finalize()
        enc = mine.Cipher(mine.algorithms.AES(b"WX812FB36C5901D3"), mine.modes.ECB()).encryptor()
        actual = base64.b64encode(enc.update(my_padded) + enc.finalize()).decode()
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
