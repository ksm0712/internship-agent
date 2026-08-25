"""Encryption for BYO API keys at rest.

The BYO-key model means real, usable Gemini/Tavily/Hunter keys live on this
server for as long as a user is signed in. The previous version stored them as
plaintext JSON (`data/web_state.json`), so anyone who could read the data
directory could read every signed-in user's keys. This wraps each key with
Fernet (AES-128-CBC + HMAC-SHA256) before it touches disk, keyed off
`INTERNSHIP_AGENT_SECRET_KEY`.
"""
from __future__ import annotations

import base64
import hashlib
import os
import warnings

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "INTERNSHIP_AGENT_SECRET_KEY"
_DEV_DEFAULT_SECRET = "local-dev-insecure-secret-key-change-me"


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _resolve_secret() -> str:
    secret = os.getenv(_ENV_VAR)
    if secret:
        return secret
    warnings.warn(
        f"{_ENV_VAR} is not set; encrypting stored API keys with an insecure "
        "default. Set it to a random value before deploying anywhere shared "
        '(e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).',
        RuntimeWarning,
        stacklevel=3,
    )
    return _DEV_DEFAULT_SECRET


class SecretBox:
    """Encrypts/decrypts short secrets (API keys) for storage in SQLite."""

    def __init__(self, secret: str | None = None) -> None:
        self._fernet = Fernet(_derive_fernet_key(secret or _resolve_secret()))

    def encrypt(self, plaintext: str | None) -> bytes | None:
        if not plaintext:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes | None) -> str | None:
        if not token:
            return None
        try:
            return self._fernet.decrypt(bytes(token)).decode("utf-8")
        except InvalidToken:
            return None
