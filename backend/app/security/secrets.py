"""Encryption of integration credentials at rest + secret reference resolution."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str | None, fallback_seed: str) -> None:
        if key:
            self._fernet = Fernet(key.encode())
        else:  # dev only: derive a stable key from another secret
            digest = hashlib.sha256(("secretbox:" + fallback_seed).encode()).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, data: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str | None) -> dict[str, Any]:
        if not token:
            return {}
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as e:
            raise ValueError("credential decryption failed (wrong ENCRYPTION_KEY?)") from e


def resolve_secret_ref(value: Any) -> Any:
    """Allow credentials to reference external secret stores instead of embedding values.

    `env:NAME` reads an environment variable. `vault:` / `aws-sm:` prefixes are reserved hooks for a
    production secret manager and intentionally fail closed here.
    """
    if not isinstance(value, str):
        return value
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if value.startswith(("vault:", "aws-sm:", "gcp-sm:")):
        raise NotImplementedError(f"secret backend for {value.split(':', 1)[0]} is not configured")
    return value
