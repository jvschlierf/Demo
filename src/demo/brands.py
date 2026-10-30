import re
import tomllib
from pathlib import Path
from typing import Any

from demo.models import Brand, Sector

DEFAULT_BRANDS_PATH = Path(__file__).with_name("brands.toml")


class BrandConfigError(ValueError):
    pass


def load_brands(path: Path = DEFAULT_BRANDS_PATH) -> dict[str, Brand]:
    """Load the tracked brands from a TOML file, keyed by brand name."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise BrandConfigError(f"{path}: invalid TOML: {e}") from e

    entries = data.get("brand", [])
    if not entries:
        raise BrandConfigError(f"{path}: no [[brand]] entries defined")

    brands: dict[str, Brand] = {}
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        brand = _parse_brand(entry, index, path)
        if brand.name.casefold() in seen:
            raise BrandConfigError(f"{path}: duplicate brand {brand.name!r}")
        seen.add(brand.name.casefold())
        brands[brand.name] = brand
    return brands


def _parse_brand(entry: dict[str, Any], index: int, path: Path) -> Brand:
    where = f"{path}: brand #{index + 1}"
    missing = [key for key in ("name", "sector") if not entry.get(key)]
    if missing:
        raise BrandConfigError(f"{where}: missing {', '.join(missing)}")
    try:
        sector = Sector(entry["sector"])
    except ValueError:
        valid = ", ".join(s.value for s in Sector)
        raise BrandConfigError(
            f"{where}: unknown sector {entry['sector']!r} (expected one of: {valid})"
        ) from None
    return Brand(
        name=entry["name"],
        sector=sector,
        ticker=entry.get("ticker"),
        aliases=tuple(entry.get("aliases", ())),
        scout=bool(entry.get("scout", True)),
    )


def match_brands(text: str, brands: dict[str, Brand]) -> list[str]:
    """Return names of brands whose name or an alias appears in text as a whole word."""
    return [name for name, brand in brands.items() if _brand_pattern(brand).search(text)]


def _brand_pattern(brand: Brand) -> re.Pattern[str]:
    terms = "|".join(re.escape(term) for term in (brand.name, *brand.aliases))
    return re.compile(rf"(?<!\w)(?:{terms})(?!\w)", re.IGNORECASE)
