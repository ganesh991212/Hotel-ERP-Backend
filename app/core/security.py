"""Password hashing, token minting and token hashing.

Adapted from vethra_api ``app/core/security.py`` with two changes:

- Argon2 runs **off the event loop** (``verify_password_async``). A verify costs about
  50 ms of CPU; called inline in an async handler it stalls every other request on the
  worker for that long, so login traffic would degrade the whole API.
- Access tokens carry an audience, so a token minted by another service that happens to
  share a secret is rejected.

Two different hashes live here and must not be confused:
- Argon2id (slow, salted) for anything a human types.
- SHA-256 (fast, unsalted) for the 256-bit random tokens we generate. They already have
  full entropy, and an unsalted digest is what allows lookup by hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()
# Verified against when an account has no password, so that branch costs the same as a
# real check and cannot be used to enumerate accounts by timing.
_DUMMY_HASH = _hasher.hash("this-account-has-no-password")

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """A missing hash means "cannot sign in with a password", never "any password"."""
    if password_hash is None:
        _safe_verify(_DUMMY_HASH, password)
        return False
    return _safe_verify(password_hash, password)


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


def _safe_verify(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_token(num_bytes: int = 32) -> str:
    """256 bits from the OS CSPRNG."""
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    """SHA-256 hex. Only the hash is stored, so a database dump cannot impersonate anyone."""
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(
    *, user_id: UUID, session_id: UUID, hotel_id: UUID | None, is_platform: bool
) -> tuple[str, int]:
    """Mint a short-lived access token. Returns (token, expires_in_seconds).

    The hotel id is read from this token on every request, never from a header, path or
    body: a client that could name its own hotel could read any hotel.

    No permissions, names or phone numbers go in here. A JWT is readable by anyone who
    sees it, and permissions are re-read per request so a demotion applies immediately.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload: dict[str, object] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "hid": str(hotel_id) if hotel_id is not None else None,
        "plat": is_platform,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), ttl


def decode_access_token(token: str) -> dict[str, object]:
    """Verify and decode. Raises ``jwt.PyJWTError`` on anything suspect.

    ``algorithms`` is pinned to one value: letting the token choose is the ``alg: none``
    family of attacks.
    """
    settings = get_settings()
    decoded: dict[str, object] = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[ALGORITHM],
        audience=settings.jwt_audience,
        options={"require": ["exp", "sub", "sid", "aud"]},
    )
    return decoded
