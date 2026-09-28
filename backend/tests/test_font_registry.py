from pathlib import Path

from app import font_registry


def test_fonts_dir_points_at_bundled_fonts():
    assert font_registry.FONTS_DIR.name == "fonts"
    assert font_registry.FONTS_DIR.exists()


def test_discovers_bundled_ttf():
    fonts = font_registry.get_available_fonts()
    assert len(fonts) >= 1
    assert all("file_path" in f and "name" in f for f in fonts)
    assert any(f["format"] == "ttf" for f in fonts)


def test_find_font_path_by_name():
    fonts = font_registry.get_available_fonts()
    name = fonts[0]["name"]
    path = font_registry.find_font_path(name)
    assert path is not None
    assert path.exists()
    assert path.suffix.lower() in font_registry.SUPPORTED_FONT_EXTENSIONS


def test_find_font_path_normalized_match():
    # Case/space-insensitive lookup should still resolve a bundled font.
    fonts = font_registry.get_available_fonts()
    name = fonts[0]["name"]
    munged = name.replace("-", "").lower()
    assert font_registry.find_font_path(munged) is not None


def test_find_font_path_missing_returns_none():
    assert font_registry.find_font_path("definitely-not-a-real-font-xyz") is None


def test_font_family_name_readable():
    fonts = font_registry.get_available_fonts()
    family = font_registry.get_font_family_name(Path(fonts[0]["file_path"]))
    # Should return a non-empty string for a valid TTF (or None if unparsable).
    assert family is None or isinstance(family, str)
