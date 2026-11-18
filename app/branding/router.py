import base64

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.branding.images import BadImage, sanitise_logo
from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, not_found
from app.core.security import now
from app.models import Hotel, HotelBranding

router = APIRouter(tags=["branding"])


class BrandingIn(BaseModel):
    address_line: str | None = Field(default=None, max_length=200)


class LogoIn(BaseModel):
    image_b64: str = Field(max_length=3_000_000)


async def get_branding(ctx: Ctx) -> HotelBranding:
    b = await ctx.db.get(HotelBranding, ctx.hotel_id)
    if not b:
        b = HotelBranding(hotel_id=ctx.hotel_id)
        ctx.db.add(b)
        await ctx.db.flush()
    return b


def branding_json(hotel: Hotel, b: HotelBranding | None) -> dict:
    return {
        "name": hotel.name,
        "address_line": b.address_line if b else None,
        "has_logo": bool(b and b.logo_png),
        "logo_version": b.logo_version if b else 0,
    }


@router.get("/branding")
async def read(ctx: Ctx = HotelCtx()) -> dict:
    return branding_json(await ctx.db.get(Hotel, ctx.hotel_id), await ctx.db.get(HotelBranding, ctx.hotel_id))


@router.put("/branding")
async def update(body: BrandingIn, ctx: Ctx = HotelCtx("settings.manage")) -> dict:
    b = await get_branding(ctx)
    before = b.address_line
    # One line on prints: collapse whitespace and newlines.
    b.address_line = " ".join((body.address_line or "").split()) or None
    b.updated_at = now()
    await ctx.audit(
        "branding.update",
        "hotel",
        ctx.hotel_id,
        before={"address_line": before},
        after={"address_line": b.address_line},
    )
    return branding_json(await ctx.db.get(Hotel, ctx.hotel_id), b)


@router.put("/branding/logo")
async def upload_logo(body: LogoIn, ctx: Ctx = HotelCtx("settings.manage")) -> dict:
    try:
        png = sanitise_logo(base64.b64decode(body.image_b64, validate=True))
    except (BadImage, ValueError) as e:
        raise bad_request("bad_image", str(e) if isinstance(e, BadImage) else "Upload a PNG or JPEG image") from e
    b = await get_branding(ctx)
    b.logo_png, b.logo_version, b.updated_at = png, b.logo_version + 1, now()
    await ctx.audit("branding.logo", "hotel", ctx.hotel_id, after={"bytes": len(png)})
    return branding_json(await ctx.db.get(Hotel, ctx.hotel_id), b)


@router.delete("/branding/logo")
async def delete_logo(ctx: Ctx = HotelCtx("settings.manage")) -> dict:
    b = await get_branding(ctx)
    b.logo_png, b.logo_version = None, b.logo_version + 1
    await ctx.audit("branding.logo_removed", "hotel", ctx.hotel_id)
    return branding_json(await ctx.db.get(Hotel, ctx.hotel_id), b)


@router.get("/branding/logo", response_class=Response)
async def logo(ctx: Ctx = HotelCtx()) -> Response:
    b = await ctx.db.get(HotelBranding, ctx.hotel_id)
    if not b or not b.logo_png:
        raise not_found("Logo")
    return Response(b.logo_png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
