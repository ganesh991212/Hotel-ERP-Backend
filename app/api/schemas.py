"""Request and response models shared across routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import RowMapping

from app.core.identifiers import validate_gstin

ClientPlatform = Literal["web", "android", "ios", "tablet", "tv", "desktop"]


class Strict(BaseModel):
    # Unknown fields are an error, not silently dropped: a typo in "kitchen_mod" must not
    # look like a successful update.
    model_config = ConfigDict(extra="forbid")


class DeviceIn(Strict):
    platform: ClientPlatform = "web"
    device_label: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - scheme name, not a secret
    expires_in: int


class InviteOut(BaseModel):
    """Shown once, to the admin who created it, to share with the person (e.g. WhatsApp)."""

    token: str
    expires_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


GstRegistration = Literal["regular", "composition", "unregistered"]


class TaxProfileIn(Strict):
    gst_registration: GstRegistration
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    legal_name: str = Field(min_length=2, max_length=200)
    state_code: str = Field(pattern=r"^[0-9]{2}$")
    service_charge_applies: bool = False
    service_charge_bp: int = Field(default=0, ge=0, le=2000, description="Basis points: 500 = 5%")
    invoice_prefix: str = Field(default="INV", pattern=r"^[A-Z0-9/-]{1,16}$")
    fy_start_month: int = Field(default=4, ge=1, le=12)

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = validate_gstin(value)
        if normalised is None:
            raise ValueError("GSTIN is not valid (format, state code or check digit)")
        return normalised

    @model_validator(mode="after")
    def _consistent(self) -> TaxProfileIn:
        if self.gst_registration == "unregistered":
            if self.gstin is not None:
                raise ValueError("an unregistered business has no GSTIN")
        elif self.gstin is None:
            raise ValueError(f"a {self.gst_registration} taxpayer must provide a GSTIN")
        if self.gstin is not None and self.gstin[:2] != self.state_code:
            raise ValueError("GSTIN state code does not match state_code")
        if not self.service_charge_applies and self.service_charge_bp != 0:
            raise ValueError("service_charge_bp must be 0 when service charge does not apply")
        return self


class TaxProfileOut(BaseModel):
    gst_registration: GstRegistration
    gstin: str | None
    legal_name: str
    state_code: str
    service_charge_applies: bool
    service_charge_bp: int
    invoice_prefix: str
    fy_start_month: int
    # Which document billing will issue: decided by registration type (docs/03).
    invoice_document: Literal["tax_invoice", "bill_of_supply", "bill"]
    updated_at: datetime


def tax_profile_out(row: RowMapping) -> TaxProfileOut:
    fields = {k: row[k] for k in TaxProfileOut.model_fields if k != "invoice_document"}
    return TaxProfileOut(**fields, invoice_document=invoice_document(row["gst_registration"]))


def invoice_document(reg: str) -> Literal["tax_invoice", "bill_of_supply", "bill"]:
    if reg == "regular":
        return "tax_invoice"
    if reg == "composition":
        return "bill_of_supply"
    return "bill"


class EntitlementsOut(BaseModel):
    plan_code: str | None
    subscription_status: str | None
    version: int
    flags: dict[str, bool]
    limits: dict[str, int | None] = Field(description="null means unlimited")
    sources: dict[str, Literal["override", "plan", "default"]] | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None


class HotelBrief(BaseModel):
    id: uuid.UUID
    name: str
    status: str


class RoleBrief(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    permissions: list[str]


class MeOut(BaseModel):
    user: UserOut
    is_platform_admin: bool
    hotel: HotelBrief | None
    role: RoleBrief | None
    outlet_ids: list[uuid.UUID] | None


class LoginOut(MeOut):
    tokens: TokenPair
