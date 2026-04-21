"""Known-vector + round-trip tests for plejd.crypto.

The golden vector was computed by running the reference algorithm
(addr_rev + addr_rev + addr_rev[:4]) through AES-128-ECB with the
``cryptography`` library against a fixed key/address pair. It locks the
*byte layout* of the keystream derivation — any accidental swap
(forgot the reverse, sliced the wrong 4 bytes, cycled on the wrong
block) changes the output.

The round-trip tests assert the stated symmetry invariant
``enc(enc(x)) == x`` and the "empty in → empty out" edge case.
"""
from __future__ import annotations

import pytest

from system_modules.device_control.plejd import crypto


# Fixed test fixtures — chosen so every byte of key + address is distinct
# from every other, making mis-slicing easy to spot in a diff.
SITE_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")  # 16 bytes
BLE_ADDR = bytes.fromhex("112233445566")                    # 6 bytes


# Golden keystream: AES-ECB(SITE_KEY, addr_rev+addr_rev+addr_rev[:4])
# where addr_rev = 66 55 44 33 22 11 and the 16-byte block is:
# 66 55 44 33 22 11 66 55 44 33 22 11 66 55 44 33
# Computed once on 2026-04-21 with cryptography 46.0.6. If this ever
# needs to change, update the comment + the constant together so the
# review trail is explicit.
GOLDEN_KEYSTREAM = bytes.fromhex("6c26f7f348dfa260d066f9a1afd87895")


# ── Keystream derivation ─────────────────────────────────────────────────


def test_derive_keystream_matches_golden_vector():
    ks = crypto.derive_keystream(SITE_KEY, BLE_ADDR)
    assert len(ks) == 16
    assert ks == GOLDEN_KEYSTREAM, (
        f"keystream drift: got {ks.hex()}, expected {GOLDEN_KEYSTREAM.hex()}. "
        "If the algorithm genuinely changed, update GOLDEN_KEYSTREAM and "
        "leave a note explaining why."
    )


def test_derive_keystream_is_deterministic():
    a = crypto.derive_keystream(SITE_KEY, BLE_ADDR)
    b = crypto.derive_keystream(SITE_KEY, BLE_ADDR)
    assert a == b


def test_derive_keystream_varies_with_address():
    ks1 = crypto.derive_keystream(SITE_KEY, BLE_ADDR)
    ks2 = crypto.derive_keystream(SITE_KEY, b"\x00" * 6)
    assert ks1 != ks2


def test_derive_keystream_varies_with_key():
    ks1 = crypto.derive_keystream(SITE_KEY, BLE_ADDR)
    ks2 = crypto.derive_keystream(b"\xff" * 16, BLE_ADDR)
    assert ks1 != ks2


# ── encrypt_decrypt symmetry ─────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    b"",
    b"\x00",
    b"\x00\x01\x02",
    b"hello",
    b"\x00" * 16,
    b"\xff" * 16,
    b"\x00" * 17,          # crosses the 16-byte block boundary
    b"\xaa" * 32,          # two full blocks
    b"Plejd " * 10,        # arbitrary
])
def test_encrypt_decrypt_is_symmetric(payload):
    ct = crypto.encrypt_decrypt(SITE_KEY, BLE_ADDR, payload)
    pt = crypto.encrypt_decrypt(SITE_KEY, BLE_ADDR, ct)
    assert pt == payload


def test_encrypt_of_known_input_matches_keystream_xor():
    """Sanity: encrypting 16 zero bytes returns the keystream itself,
    because x ^ 0 == x. This catches off-by-one errors in the XOR loop
    without needing a second golden vector."""
    payload = b"\x00" * 16
    ct = crypto.encrypt_decrypt(SITE_KEY, BLE_ADDR, payload)
    assert ct == GOLDEN_KEYSTREAM


def test_encrypt_cycles_keystream_for_long_payloads():
    """33-byte payload forces two full cycles plus one byte. The cycled
    byte must match ``keystream[0] ^ payload[32]``."""
    payload = bytes(range(33))
    ct = crypto.encrypt_decrypt(SITE_KEY, BLE_ADDR, payload)
    assert ct[32] == (payload[32] ^ GOLDEN_KEYSTREAM[0])


def test_empty_input_returns_empty():
    assert crypto.encrypt_decrypt(SITE_KEY, BLE_ADDR, b"") == b""


# ── Input validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_key", [b"", b"\x00", b"\x00" * 15, b"\x00" * 17])
def test_bad_key_length_raises(bad_key):
    with pytest.raises(crypto.PlejdCryptoError):
        crypto.derive_keystream(bad_key, BLE_ADDR)


@pytest.mark.parametrize("bad_addr", [b"", b"\x00" * 5, b"\x00" * 7])
def test_bad_address_length_raises(bad_addr):
    with pytest.raises(crypto.PlejdCryptoError):
        crypto.derive_keystream(SITE_KEY, bad_addr)


# ── Address parser ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("11:22:33:44:55:66", b"\x11\x22\x33\x44\x55\x66"),
    ("aa-bb-cc-dd-ee-ff", b"\xaa\xbb\xcc\xdd\xee\xff"),
    ("AABBCCDDEEFF",      b"\xaa\xbb\xcc\xdd\xee\xff"),
])
def test_parse_addr(raw, expected):
    assert crypto.parse_addr(raw) == expected


@pytest.mark.parametrize("bad", ["short", "zzzzzzzzzzzz", "", "11:22:33:44:55"])
def test_parse_addr_rejects_junk(bad):
    with pytest.raises(crypto.PlejdCryptoError):
        crypto.parse_addr(bad)


def test_format_addr_roundtrips():
    original = "AA:BB:CC:DD:EE:FF"
    raw = crypto.parse_addr(original)
    assert crypto.format_addr(raw) == original
