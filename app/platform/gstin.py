"""GSTIN format and checksum (pure)."""

import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_FORMAT = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        prod = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return _CHARS[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    g = gstin.strip().upper()
    return bool(_FORMAT.match(g)) and checksum_char(g[:14]) == g[14]
