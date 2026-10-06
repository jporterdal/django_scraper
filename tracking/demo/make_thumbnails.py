"""Generate the demo card thumbnails from ``data/metadata.json``.

Run after editing the metadata entries; the generated SVGs are committed:

    python tracking/demo/make_thumbnails.py

Standard library only (no Django, no image library). Each card is a
rounded rectangle in the entry's colour with a simple motif glyph and the
card name, written to ``tracking/static/<thumbnail>``.
"""

import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
METADATA = HERE / "data" / "metadata.json"
STATIC_ROOT = HERE.parent / "static"

# Simple glyphs drawn in a 100x100 box centred in the art window.
MOTIFS = {
    "flame": '<path d="M50 8 C62 30 80 40 74 66 C70 84 58 92 50 92 C42 92 28 84 26 66 C24 50 36 44 40 30 C44 42 50 46 54 40 C58 32 54 20 50 8 Z"/>',
    "shield": '<path d="M50 8 L86 22 L82 58 C78 76 64 88 50 94 C36 88 22 76 18 58 L14 22 Z"/>',
    "eye": '<path d="M6 50 C26 20 74 20 94 50 C74 80 26 80 6 50 Z"/><circle cx="50" cy="50" r="16" fill="#ffffff" fill-opacity="0.85"/><circle cx="50" cy="50" r="7"/>',
    "sun": '<circle cx="50" cy="50" r="22"/>' + "".join(
        # Ray triangles computed directly (no rotate()), so every SVG renderer agrees.
        '<path d="M{:.1f} {:.1f} L{:.1f} {:.1f} L{:.1f} {:.1f} Z"/>'.format(
            50 + 46 * math.cos(a), 50 + 46 * math.sin(a),
            50 + 28 * math.cos(a - 0.2), 50 + 28 * math.sin(a - 0.2),
            50 + 28 * math.cos(a + 0.2), 50 + 28 * math.sin(a + 0.2),
        )
        for a in (math.radians(deg) for deg in range(0, 360, 45))
    ),
    "crown": '<path d="M10 78 L16 30 L36 54 L50 20 L64 54 L84 30 L90 78 Z"/><rect x="10" y="80" width="80" height="10" rx="3"/>',
    "star": '<path d="M50 6 L61 38 L95 38 L67 58 L78 92 L50 72 L22 92 L33 58 L5 38 L39 38 Z"/>',
    "leaf": '<path d="M50 94 C50 70 50 50 50 30 M50 94 C14 70 12 30 50 6 C88 30 86 70 50 94 Z"/>',
}

TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="244" height="340" viewBox="0 0 244 340" role="img" aria-label="{label}">
  <rect x="2" y="2" width="240" height="336" rx="16" fill="#1b1b1b"/>
  <rect x="12" y="12" width="220" height="316" rx="10" fill="{color}"/>
  <rect x="22" y="22" width="200" height="30" rx="6" fill="#ffffff" fill-opacity="0.88"/>
  <text x="32" y="43" font-family="Georgia, serif" font-size="{name_size}" fill="#1b1b1b">{name}</text>
  <rect x="22" y="60" width="200" height="150" rx="6" fill="#000000" fill-opacity="0.25"/>
  <g transform="translate(72 85)" fill="#ffffff" fill-opacity="0.9">{motif}</g>
  <rect x="22" y="218" width="200" height="22" rx="5" fill="#ffffff" fill-opacity="0.8"/>
  <text x="30" y="234" font-family="Georgia, serif" font-size="11" fill="#1b1b1b">{type_line}</text>
  <rect x="22" y="248" width="200" height="70" rx="6" fill="#ffffff" fill-opacity="0.8"/>
  <text x="30" y="270" font-family="Georgia, serif" font-size="11" font-style="italic" fill="#333333">{set_name}</text>
  <text x="30" y="306" font-family="Georgia, serif" font-size="10" fill="#555555">Demo card · not a real product</text>
</svg>
"""


def render(entry):
    name = entry["name"]
    return TEMPLATE.format(
        label=escape(f"{name} (demo card)"),
        color=entry["color"],
        name=escape(name),
        name_size=14 if len(name) <= 22 else 11,
        motif=MOTIFS[entry["motif"]],
        type_line=escape(entry["type_line"]),
        set_name=escape(entry["set"]),
    )


def main():
    entries = json.loads(METADATA.read_text(encoding="utf-8"))
    for entry in entries:
        out = STATIC_ROOT / entry["thumbnail"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(entry), encoding="utf-8")
        print(f"wrote {out.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
