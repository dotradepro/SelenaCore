"""Plejd LIGHT-characteristic cipher — AES-128-ECB keystream XOR.

Plejd encrypts every data frame written to the LIGHT characteristic with
the same algorithm; the scheme is symmetric so the same function decrypts
incoming notifications. The keystream is derived from the *BLE address of
the connected device*, not from the destination mesh output — every
frame exchanged over the active GATT connection uses the connected
device's address to key the cipher.

Algorithm (verified against bolstad/plejd-homebridge, pyplejd, ha-plejd):

    1. Reverse the 6-byte BLE address (hci reports it big-endian; the
       frame expects little-endian).
    2. Build a 16-byte block: ``addr_rev || addr_rev || addr_rev[:4]``.
    3. Encrypt that block with AES-128-ECB and the 16-byte site key.
       The output is the per-session keystream.
    4. XOR the payload byte-by-byte against the cyclic keystream.

Encryption is identical to decryption — XOR with the same keystream
cancels itself. That's why the function is named ``encrypt_decrypt``.

This module is pure — no IO, no bleak import, no network. The only
runtime dependency is ``cryptography`` (already pinned by the
secrets_vault module).
"""
from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

#: Size of the AES block / keystream period, in bytes.
KEYSTREAM_BLOCK = 16

#: BLE address is exactly 6 bytes on-the-wire.
ADDR_LEN = 6


class PlejdCryptoError(ValueError):
    """Raised for bad-shape input (wrong-length key or address)."""


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) != 16:
        raise PlejdCryptoError(
            f"site key must be exactly 16 bytes, got {len(key) if hasattr(key, '__len__') else '?'}",
        )


def _check_addr(addr: bytes) -> None:
    if not isinstance(addr, (bytes, bytearray)) or len(addr) != ADDR_LEN:
        raise PlejdCryptoError(
            f"BLE address must be exactly 6 bytes, got {len(addr) if hasattr(addr, '__len__') else '?'}",
        )


def derive_keystream(key: bytes, addr: bytes) -> bytes:
    """Return the 16-byte keystream for ``(key, addr)``.

    Exposed separately so the gateway can derive once per connection and
    cache — computing AES is fast, but repeated calls show up on cold
    Raspberry Pi profiles when every notification triggers a fresh
    derivation.
    """
    _check_key(key)
    _check_addr(addr)
    addr_rev = bytes(reversed(addr))
    block = addr_rev + addr_rev + addr_rev[:4]
    assert len(block) == KEYSTREAM_BLOCK, "algorithm invariant: 6+6+4 == 16"
    encryptor = Cipher(algorithms.AES(bytes(key)), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def encrypt_decrypt(key: bytes, addr: bytes, data: bytes) -> bytes:
    """XOR ``data`` against the (key, addr) keystream.

    Symmetric: ``encrypt_decrypt(k, a, encrypt_decrypt(k, a, x)) == x``.
    Empty input returns empty output. Arbitrary-length input OK —
    keystream cycles modulo 16.
    """
    ks = derive_keystream(key, addr)
    return bytes(b ^ ks[i % KEYSTREAM_BLOCK] for i, b in enumerate(data))


# ── Convenience: parse + format 6-byte BLE addresses ──────────────────────


def parse_addr(mac: str) -> bytes:
    """Parse a ``"AA:BB:CC:DD:EE:FF"`` / ``"aabbccddeeff"`` string to 6 bytes.

    Big-endian — the wire order hci reports. ``encrypt_decrypt`` reverses
    internally when deriving the keystream, so callers should pass the
    address exactly as it appears in their advertisement / pairing UI.
    """
    s = mac.strip().replace(":", "").replace("-", "").lower()
    if len(s) != ADDR_LEN * 2 or any(c not in "0123456789abcdef" for c in s):
        raise PlejdCryptoError(f"invalid BLE address: {mac!r}")
    return bytes.fromhex(s)


def format_addr(addr: bytes) -> str:
    """Inverse of parse_addr — "AA:BB:CC:DD:EE:FF" uppercase."""
    _check_addr(addr)
    return ":".join(f"{b:02X}" for b in addr)
