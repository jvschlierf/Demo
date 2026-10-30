from pathlib import Path

import pytest

from demo.brands import BrandConfigError, load_brands, match_brands
from demo.models import Brand, Sector


def write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "brands.toml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def brands() -> dict[str, Brand]:
    return {
        "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE", ("Converse",)),
        "Kering": Brand("Kering", Sector.LUXURY, "KER.PA", ("Gucci", "Saint Laurent")),
        "Amer Sports": Brand("Amer Sports", Sector.SPORTSWEAR, "AS", ("Arc'teryx",)),
    }


def test_default_file_loads_core_brands() -> None:
    brands = load_brands()
    for name in ("LVMH", "Kering", "Burberry", "Adidas", "Nike", "Skechers"):
        assert name in brands
    assert brands["Burberry"].sector is Sector.LUXURY
    assert brands["Skechers"].ticker is None


def test_load_parses_fields(tmp_path: Path) -> None:
    path = write_toml(
        tmp_path,
        """
        [[brand]]
        name = "Nike"
        sector = "sportswear"
        ticker = "NKE"
        aliases = ["Converse"]
        """,
    )
    assert load_brands(path) == {
        "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE", ("Converse",)),
    }


def test_load_empty_file_errors(tmp_path: Path) -> None:
    with pytest.raises(BrandConfigError, match="no \\[\\[brand\\]\\] entries"):
        load_brands(write_toml(tmp_path, ""))


def test_load_invalid_toml_errors(tmp_path: Path) -> None:
    with pytest.raises(BrandConfigError, match="invalid TOML"):
        load_brands(write_toml(tmp_path, "[[brand]\nname = "))


def test_load_missing_field_errors(tmp_path: Path) -> None:
    path = write_toml(tmp_path, '[[brand]]\nname = "Nike"\n')
    with pytest.raises(BrandConfigError, match="brand #1: missing sector"):
        load_brands(path)


def test_load_unknown_sector_errors(tmp_path: Path) -> None:
    path = write_toml(tmp_path, '[[brand]]\nname = "Nike"\nsector = "fintech"\n')
    with pytest.raises(BrandConfigError, match="unknown sector 'fintech'"):
        load_brands(path)


def test_load_duplicate_brand_errors(tmp_path: Path) -> None:
    path = write_toml(
        tmp_path,
        """
        [[brand]]
        name = "Nike"
        sector = "sportswear"
        [[brand]]
        name = "NIKE"
        sector = "sportswear"
        """,
    )
    with pytest.raises(BrandConfigError, match="duplicate brand 'NIKE'"):
        load_brands(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nike cuts guidance", ["Nike"]),
        ("NIKE shares slide", ["Nike"]),
        ("Converse sales fall in Europe", ["Nike"]),
        ("Gucci creative director leaves", ["Kering"]),
        ("Arc'teryx opens London flagship", ["Amer Sports"]),
        ("Nike and Gucci collab rumoured", ["Nike", "Kering"]),
    ],
)
def test_match_brands_finds_names_and_aliases(
    brands: dict[str, Brand], text: str, expected: list[str]
) -> None:
    assert match_brands(text, brands) == expected


@pytest.mark.parametrize("text", ["", "Nikebook launches", "Guccione interview", "no brands here"])
def test_match_brands_ignores_partial_words(brands: dict[str, Brand], text: str) -> None:
    assert match_brands(text, brands) == []


def test_scout_defaults_on_and_can_be_switched_off(tmp_path: Path) -> None:
    path = write_toml(
        tmp_path,
        """
        [[brand]]
        name = "Nike"
        sector = "sportswear"
        [[brand]]
        name = "Puma"
        sector = "sportswear"
        scout = false
        """,
    )
    brands = load_brands(path)
    assert brands["Nike"].scout is True
    assert brands["Puma"].scout is False
    assert all(b.scout for b in load_brands().values())
