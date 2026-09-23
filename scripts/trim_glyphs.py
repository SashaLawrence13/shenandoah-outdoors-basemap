#!/usr/bin/env python3
"""Latin-only glyph sets for the Topo and Satellite labels.

MapLibre's offline download fetches all 256 glyph ranges of every font a
style uses; the full Noto Sans ranges (CJK fallback included) are ~34 MB
per font. Place names here are Latin, so the three ranges that matter
are copied from OpenMapTiles' font release (noto-sans.zip, v2.0, SIL OFL)
and every other range is a valid, empty glyph PBF.

    gh release download v2.0 -R openmaptiles/fonts -p noto-sans.zip
    unzip noto-sans.zip -d fonts && python3 scripts/trim_glyphs.py fonts glyphs
"""
import os, shutil, sys

SRC, OUT = sys.argv[1], sys.argv[2]
KEEP = {"0-255", "256-511", "8192-8447"}

def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)

def field_str(num, s):
    b = s.encode(); return bytes([num << 3 | 2]) + varint(len(b)) + b

def empty_pbf(name, rng):
    stack = field_str(1, name) + field_str(2, rng)
    return bytes([1 << 3 | 2]) + varint(len(stack)) + stack

for font in ["Noto Sans Regular", "Noto Sans Italic", "Noto Sans Bold"]:
    d = os.path.join(OUT, font); os.makedirs(d, exist_ok=True)
    for i in range(256):
        rng = f"{i * 256}-{i * 256 + 255}"
        if rng in KEEP:
            shutil.copy(os.path.join(SRC, font, f"{rng}.pbf"), os.path.join(d, f"{rng}.pbf"))
        else:
            open(os.path.join(d, f"{rng}.pbf"), "wb").write(empty_pbf(font, rng))
