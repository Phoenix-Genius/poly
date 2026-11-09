"""Encrypt secrets (users' Polymarket keys) at rest with AES-256-GCM, for the hosted multi-user mode.

The 32-byte key comes from the POLY_SECRET_KEY environment variable (64 hex characters), which only the server's
own .env holds. Encrypted values look like "enc:v1:<nonce hex>:<ciphertext+tag hex>". Without POLY_SECRET_KEY
nothing is encrypted (the single-owner app on your own computer keeps plain .env files, as before).
"""
from __future__ import annotations

import os
import secrets

PREFIX = "enc:v1:"


def _key() -> bytes | None:
    raw = os.environ.get("POLY_SECRET_KEY", "").strip()
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise ValueError("POLY_SECRET_KEY must be 64 hex characters (32 bytes)") from None
    if len(key) != 32:
        raise ValueError("POLY_SECRET_KEY must be 64 hex characters (32 bytes)")
    return key


def enabled() -> bool:
    return _key() is not None


def encrypt(plain: str) -> str:
    """The encrypted form of `plain`, or `plain` itself when no POLY_SECRET_KEY is set (or it's empty)."""
    key = _key()
    if not key or not plain or plain.startswith(PREFIX):
        return plain
    from Crypto.Cipher import AES          # pycryptodome, only needed when keys are encrypted
    nonce = secrets.token_bytes(12)
    box = AES.new(key, AES.MODE_GCM, nonce=nonce)
    data, tag = box.encrypt_and_digest(plain.encode())
    return f"{PREFIX}{nonce.hex()}:{(data + tag).hex()}"


def decrypt(value: str) -> str:
    """The plain text of an encrypted value; anything not encrypted is returned as it is."""
    if not value or not value.startswith(PREFIX):
        return value
    key = _key()
    if not key:
        raise ValueError("This key is encrypted: set POLY_SECRET_KEY (the server's secret) to use it")
    try:
        from Crypto.Cipher import AES
        nonce_hex, blob_hex = value[len(PREFIX):].split(":", 1)
        blob = bytes.fromhex(blob_hex)
        box = AES.new(key, AES.MODE_GCM, nonce=bytes.fromhex(nonce_hex))
        return box.decrypt_and_verify(blob[:-16], blob[-16:]).decode()
    except (ValueError, KeyError):
        raise ValueError("Couldn't decrypt a stored key (wrong POLY_SECRET_KEY?)") from None


def new_key() -> str:
    """A fresh POLY_SECRET_KEY value."""
    return secrets.token_hex(32)


if __name__ == "__main__":     # python -m poly_bot.secretbox  ->  a new POLY_SECRET_KEY
    print(f"POLY_SECRET_KEY={new_key()}")
