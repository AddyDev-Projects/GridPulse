"""Password hashing (Argon2id), password policy, opaque tokens, and privacy-preserving client keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

# argon2-cffi defaults are the RFC 9106 "low memory" profile of Argon2id (t=3, m=64 MiB, p=4).
_hasher = PasswordHasher()
# Verified against when the username does not exist, so response time does not reveal accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))

MIN_PASSWORD = 12
MAX_PASSWORD = 256
_COMMON = {"password1234", "123456789012", "qwertyuiopas", "gridpulse123", "administrator", "letmein12345",
           "passwordpassword", "welcome12345", "changeme1234", "mangaluru123", "mangalore123"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(username: str, password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        problems.append(f"must be at most {MAX_PASSWORD} characters")
    if password.strip() != password:
        problems.append("must not start or end with whitespace")
    if len(set(password)) < 5:
        problems.append("is too repetitive")
    low = password.lower()
    if username and username.lower() in low:
        problems.append("must not contain the username")
    if low in _COMMON:
        problems.append("is too common")
    return problems


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str | None, expected_hash: str) -> bool:
    return bool(token) and hmac.compare_digest(token_hash(token), expected_hash)


def client_key(bucket: str, raw: str) -> str:
    """HMAC of a client identifier (IP address, username) under the deployment secret."""
    secret = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(secret, f"{bucket}:{raw}".encode(), hashlib.sha256).hexdigest()
