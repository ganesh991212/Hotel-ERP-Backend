"""Pure-function tests: no database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.schemas import TaxProfileIn
from app.core.identifiers import normalize_phone, parse_login, validate_gstin
from app.core.pagination import decode_cursor, encode_cursor
from app.core.permissions import PERMISSIONS, SYSTEM_ROLES
from app.core.security import decode_access_token, issue_access_token


@pytest.mark.parametrize("gstin", ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", " 27aapfu0939f1zv "])
def test_valid_gstins(gstin: str) -> None:
    assert validate_gstin(gstin) == gstin.strip().upper()


@pytest.mark.parametrize(
    "gstin",
    [
        "27AAPFU0939F1ZW",  # wrong check character
        "40AAPFU0939F1ZV",  # no state 40
        "27AAPFU0939F1XV",  # 14th character must be Z
        "27AAPF0939F1ZV",  # too short
        "",
    ],
)
def test_invalid_gstins(gstin: str) -> None:
    assert validate_gstin(gstin) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("5876543210", None),  # Indian mobiles start 6-9
        ("12345", None),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_phone_normalisation(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


def test_login_parsing() -> None:
    assert parse_login("A.B@Example.COM").value == "a.b@example.com"  # type: ignore[union-attr]
    assert parse_login("98765 43210").kind == "phone"  # type: ignore[union-attr]
    assert parse_login("not-an-email@") is None


def test_tax_profile_rules() -> None:
    TaxProfileIn(gst_registration="unregistered", legal_name="Dhaba", state_code="36")
    TaxProfileIn(
        gst_registration="composition", gstin="27AAPFU0939F1ZV", legal_name="X Y", state_code="27"
    )
    with pytest.raises(ValidationError, match="must provide a GSTIN"):
        TaxProfileIn(gst_registration="regular", legal_name="X Y", state_code="27")
    with pytest.raises(ValidationError, match="no GSTIN"):
        TaxProfileIn(
            gst_registration="unregistered",
            gstin="27AAPFU0939F1ZV",
            legal_name="X Y",
            state_code="27",
        )
    with pytest.raises(ValidationError, match="does not match"):
        TaxProfileIn(
            gst_registration="regular", gstin="27AAPFU0939F1ZV", legal_name="X Y", state_code="29"
        )
    with pytest.raises(ValidationError, match="check digit"):
        TaxProfileIn(
            gst_registration="regular", gstin="27AAPFU0939F1ZW", legal_name="X Y", state_code="27"
        )


def test_system_roles_use_registered_permissions() -> None:
    for role in SYSTEM_ROLES:
        assert role.permissions <= set(PERMISSIONS), role.code
    admin = next(r for r in SYSTEM_ROLES if r.code == "hotel_admin")
    assert admin.permissions == set(PERMISSIONS) - {"kitchen.work"}


def test_cursor_round_trip() -> None:
    stamp, row_id = datetime(2026, 11, 3, 10, 30, tzinfo=UTC), uuid.uuid4()
    assert decode_cursor(encode_cursor(stamp, row_id)) == (stamp, row_id)


def test_access_token_carries_no_permissions() -> None:
    token, _ = issue_access_token(
        user_id=uuid.uuid4(), session_id=uuid.uuid4(), hotel_id=uuid.uuid4(), is_platform=False
    )
    claims = decode_access_token(token)
    assert set(claims) == {"sub", "sid", "hid", "plat", "aud", "iat", "exp"}
