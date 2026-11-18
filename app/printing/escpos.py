"""Pure print layout: one block list renders to ESC/POS bytes (thermal printers) and is sent to the client
for PDF rendering (any printer via the OS print dialog). One layout, identical output everywhere.

Blocks are tuples:
  ("title", text)          centred, double size, bold
  ("center", text)         centred
  ("text", text)           left
  ("bold", text)           left, bold, double height (kitchen-readable item lines)
  ("row", left, right)     left text, right-aligned text on the same line (wraps left if needed)
  ("total", left, right)   like row, bold, double height
  ("divider",)
  ("feed", n)
  ("qr", data)             QR code, centred (native ESC/POS QR command on thermal printers)
  ("cut",)
"""

import textwrap

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
ALIGN = {"left": ESC + b"a\x00", "center": ESC + b"a\x01"}
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
SIZE_NORMAL, SIZE_DOUBLE, SIZE_TALL = GS + b"!\x00", GS + b"!\x11", GS + b"!\x01"
CUT = GS + b"V\x42\x00"  # partial cut after feeding

# Characters per line at Font A: 58 mm ≈ 32, 80 mm ≈ 48.
WIDTHS = {58: 32, 80: 48}


def _ascii(s: str) -> str:
    """Most thermal printers have no Unicode font. Rupee sign and dashes are spelled out; anything else
    non-ASCII becomes '?'. (Regional-language tickets need raster/image printing; see docs/07.)"""
    s = s.replace("₹", "Rs.").replace("–", "-").replace("—", "-").replace("·", "-").replace("×", "x")
    return s.encode("ascii", "replace").decode("ascii")


def _row(left: str, right: str, width: int) -> list[str]:
    room = width - len(right) - 1
    if room < 8:
        return [*(textwrap.wrap(left, width) or [""]), right.rjust(width)]
    parts = textwrap.wrap(left, room) or [""]
    out = list(parts[:-1])
    out.append(parts[-1].ljust(room) + " " + right)
    return out


def to_lines(blocks: list[tuple], width: int) -> list[tuple[str, str]]:
    """(style, text) lines; used by ESC/POS and by tests. Double-size text gets half the width."""
    lines: list[tuple[str, str]] = []
    for b in blocks:
        kind = b[0]
        if kind == "title":
            lines += [("title", t) for t in textwrap.wrap(_ascii(b[1]), width // 2) or [""]]
        elif kind == "center":
            lines += [("center", t) for t in textwrap.wrap(_ascii(b[1]), width) or [""]]
        elif kind == "text":
            lines += [("text", t) for t in textwrap.wrap(_ascii(b[1]), width) or [""]]
        elif kind == "bold":
            lines += [("bold", t) for t in textwrap.wrap(_ascii(b[1]), width) or [""]]
        elif kind in ("row", "total"):
            style = "total" if kind == "total" else "text"
            lines += [(style, t) for t in _row(_ascii(b[1]), _ascii(b[2]), width)]
        elif kind == "divider":
            lines.append(("text", "-" * width))
        elif kind == "feed":
            lines += [("text", "")] * int(b[1])
        elif kind == "cut":
            lines.append(("cut", ""))
        elif kind == "qr":
            lines.append(("qr", b[1]))
    return lines


def qr_command(data: str, module: int = 6) -> bytes:
    """GS ( k: model 2, module size, error correction M, store, print. Supported by Epson-compatible printers."""
    payload = data.encode("ascii", "replace")
    n = len(payload) + 3
    return (
        GS
        + b"(k\x04\x00\x31\x41\x32\x00"  # model 2
        + GS
        + b"(k\x03\x00\x31\x43"
        + bytes([module])  # module size
        + GS
        + b"(k\x03\x00\x31\x45\x31"  # error correction M
        + GS
        + b"(k"
        + bytes([n % 256, n // 256])
        + b"\x31\x50\x30"
        + payload  # store
        + GS
        + b"(k\x03\x00\x31\x51\x30"  # print
    )


def render(blocks: list[tuple], paper_mm: int = 80) -> bytes:
    width = WIDTHS.get(paper_mm, 48)
    out = bytearray(INIT)
    for style, text in to_lines(blocks, width):
        if style == "cut":
            out += b"\n\n\n" + CUT
            continue
        if style == "qr":
            out += ALIGN["center"] + qr_command(text, module=8 if paper_mm >= 80 else 6) + b"\n"
            continue
        if style == "title":
            out += ALIGN["center"] + SIZE_DOUBLE + BOLD_ON
        elif style == "center":
            out += ALIGN["center"] + SIZE_NORMAL + BOLD_OFF
        elif style in ("bold", "total"):
            out += ALIGN["left"] + SIZE_TALL + BOLD_ON
        else:
            out += ALIGN["left"] + SIZE_NORMAL + BOLD_OFF
        out += text.encode("ascii") + b"\n"
    out += SIZE_NORMAL + BOLD_OFF + ALIGN["left"]
    return bytes(out)
