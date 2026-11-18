import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings

_ph = PasswordHasher()  # argon2id defaults


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw_hash: str, pw: str) -> bool:
    try:
        return _ph.verify(pw_hash, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def make_access_token(user_id: UUID, hotel_id: UUID | None, platform: bool) -> str:
    s = get_settings()
    claims = {
        "sub": str(user_id),
        "hid": str(hotel_id) if hotel_id else None,
        "plt": platform,
        "typ": "access",
        "exp": now() + timedelta(minutes=s.access_token_minutes),
        "iat": now(),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return claims
