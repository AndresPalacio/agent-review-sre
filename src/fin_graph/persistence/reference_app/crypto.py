"""Injected encryption boundary for provider credentials."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet


@runtime_checkable
class SecretCipher(Protocol):
    def encrypt(self, secret: str) -> str: ...

    def decrypt(self, encrypted: str) -> str: ...


class FernetSecretCipher:
    """Authenticated symmetric encryption backed by ``cryptography`` Fernet."""

    def __init__(self, key: bytes | str) -> None:
        self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)

    def encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")
