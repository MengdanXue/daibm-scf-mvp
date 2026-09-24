from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from datetime import datetime


class InvalidCredentials(Exception):
    pass


class AuthenticationRequired(Exception):
    pass


class AccountLocked(InvalidCredentials):
    """Too many failed sign-ins; the account is locked until ``locked_until``."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: uuid.UUID
    username: str
    display_name: str
    role: str
    organization_id: uuid.UUID
    organization_code: str
    organization_name: str


@dataclass(frozen=True)
class LoginResult:
    user: AuthenticatedUser
    token: str
    expires_at: datetime


def hash_password(
    password: str,
    salt: bytes | None = None,
) -> tuple[str, str]:
    active_salt = salt or os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=active_salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return derived.hex(), active_salt.hex()


def verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    try:
        actual_hash, _ = hash_password(password, bytes.fromhex(salt_hex))
    except ValueError:
        return False
    return hmac.compare_digest(actual_hash, expected_hash)
