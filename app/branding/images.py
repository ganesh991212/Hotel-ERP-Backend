"""Logo sanitising and thermal rasterising (pure functions over bytes; Pillow)."""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SIDE = 512


class BadImage(ValueError):
    pass


def sanitise_logo(raw: bytes) -> bytes:
    """Decode, flatten transparency onto white, shrink to <=512 px, re-encode as PNG.
    Re-encoding means we never store or print the uploaded bytes themselves."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BadImage("Logo must be under 2 MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise BadImage("Upload a PNG or JPEG image") from e
    if img.format not in ("PNG", "JPEG", "WEBP"):
        raise BadImage("Upload a PNG or JPEG image")
    img = ImageOps.exif_transpose(img).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def thermal_raster(png: bytes, width_dots: int) -> bytes:
    """ESC/POS 'GS v 0' raster of the logo, dithered to 1 bit, centred by the caller's alignment."""
    img = Image.open(io.BytesIO(png)).convert("L")
    w = min(width_dots, img.width * 2)
    w -= w % 8
    h = max(1, round(img.height * w / img.width))
    img = img.resize((w, h)).convert("1")  # Floyd-Steinberg dithering
    bits = ImageOps.invert(img.convert("L")).convert("1").tobytes()  # 1 = black dot
    x_bytes = w // 8
    return b"\x1dv0\x00" + bytes([x_bytes % 256, x_bytes // 256, h % 256, h // 256]) + bits
