"""Normalising login identifiers and validating GSTINs.

Staff sign in with an email **or** a phone number: many waiters and chefs have no email.
Both are normalised before storage and lookup, so ``+91 98765 43210`` and ``9876543210``
are the same account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from email_validator import EmailNotValidError, validate_email

_DIGITS = re.compile(r"\D+")


@dataclass(frozen=True)
class LoginId:
    kind: Literal["email", "phone"]
    value: str


def normalize_phone(raw: str) -> str | None:
    """Return E.164 for Indian numbers, or ``None`` if it is not a plausible phone.

    Accepts ``9876543210``, ``09876543210``, ``919876543210`` and ``+91 98765-43210``.
    Other country codes are accepted when given with a leading ``+`` (8 to 15 digits).
    """
    stripped = raw.strip()
    digits = _DIGITS.sub("", stripped)
    if stripped.startswith("+"):
        if digits.startswith("91") and len(digits) == 12:
            return _indian(digits[2:])
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return _indian(digits)
    if len(digits) == 11 and digits.startswith("0"):
        return _indian(digits[1:])
    if len(digits) == 12 and digits.startswith("91"):
        return _indian(digits[2:])
    return None


def _indian(ten: str) -> str | None:
    # Indian mobile numbers start with 6 to 9.
    return f"+91{ten}" if len(ten) == 10 and ten[0] in "6789" else None


def normalize_email(raw: str) -> str | None:
    try:
        result = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


def parse_login(raw: str) -> LoginId | None:
    if "@" in raw:
        email = normalize_email(raw)
        return LoginId("email", email) if email else None
    phone = normalize_phone(raw)
    return LoginId("phone", phone) if phone else None


# --- GSTIN ---------------------------------------------------------------------------
# Format: 2-digit state code, 10-character PAN, entity number, 'Z', check character.
_GSTIN_RE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")
_GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 01-38 are the states and union territories; 97 is "other territory", 99 is the
# centre jurisdiction.
_VALID_STATE_CODES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}


def gstin_check_char(first14: str) -> str:
    """The GSTIN check character (mod-36, alternating weights 1 and 2)."""
    total = 0
    for index, char in enumerate(first14):
        product = _GSTIN_CHARSET.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARSET[(36 - total % 36) % 36]


def validate_gstin(raw: str) -> str | None:
    """Return the normalised GSTIN, or ``None`` when format, state or checksum is wrong."""
    gstin = raw.strip().upper()
    match = _GSTIN_RE.match(gstin)
    if match is None or match.group(1) not in _VALID_STATE_CODES:
        return None
    if gstin_check_char(gstin[:14]) != gstin[14]:
        return None
    return gstin
