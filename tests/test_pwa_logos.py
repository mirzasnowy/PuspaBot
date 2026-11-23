"""Connector/app logos (Nero, 24 Nov): real official PNGs,
transparent, and visually the same size.

The glyph in each file was normalized when the file was made
(longest side 112px on a 128px transparent canvas), so the
logos read equally big at the same display size instead of
one glyph dwarfing another. This test locks that: every logo
must be a transparent PNG whose visible glyph is 108-116px
on its longest side, and the app must not render them on a
white tile.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGOS = ["gmail", "drive", "calendar", "telegram", "whatsapp"]


def _glyph_size(path: Path) -> tuple[int, int]:
    image = Image.open(path).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    assert bbox is not None, f"{path.name} is fully transparent"
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def test_logos_are_transparent_and_visually_equal():
    sizes = {}
    for name in LOGOS:
        for tree in ("apps/pwa/static/logos", "puspa/pwa/static/logos"):
            path = ROOT / tree / f"{name}.png"
            assert path.is_file(), f"missing logo: {path}"
            assert Image.open(path).format == "PNG"
            width, height = _glyph_size(path)
            sizes.setdefault(name, set()).add((width, height))
    # Both trees carry the same file: one size per logo.
    assert all(len(v) == 1 for v in sizes.values())
    # Every glyph's longest side is ~112px (within 4px of
    # rounding): visually the same size, not just the same box.
    for name, size in sizes.items():
        longest = max(next(iter(size)))
        assert 108 <= longest <= 116, f"{name} glyph longest side is {longest}px"


def test_logos_have_no_white_tile_wrapper():
    styles = (ROOT / "apps/pwa/src/styles.css").read_text()
    assert "logo-tile" not in styles
    screen = (ROOT / "apps/pwa/src/screens/ConnectorsScreen.tsx").read_text()
    assert "logo-tile" not in screen
    assert "app-logo" in screen
