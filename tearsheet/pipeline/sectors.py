"""
Sector configuration loader.

A sector is defined declaratively in `sectors/<name>.yaml` (a display name, a
description, and a `companies` map of ticker → metadata). Adding a new sector
means dropping in a YAML file — no Python changes. See sectors/payments.yaml
for the schema and field documentation.
"""
from __future__ import annotations
import os
from typing import Optional

import yaml

# sectors/ lives at the project root (one level above the pipeline package)
SECTORS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sectors"
)


def available_sectors() -> list[str]:
    """Return the names of all sector configs found on disk."""
    if not os.path.isdir(SECTORS_DIR):
        return []
    return sorted(
        f[:-5] for f in os.listdir(SECTORS_DIR)
        if f.endswith(".yaml") or f.endswith(".yml")
    )


def _config_path(sector: str) -> Optional[str]:
    for ext in (".yaml", ".yml"):
        path = os.path.join(SECTORS_DIR, f"{sector}{ext}")
        if os.path.exists(path):
            return path
    return None


def load_sector(sector: str) -> dict:
    """
    Load a sector config. Returns a dict with keys:
      name         display name (str)
      description  one-line description (str)
      companies    dict[ticker -> meta dict]

    Raises FileNotFoundError with the list of available sectors if not found.
    """
    path = _config_path(sector)
    if not path:
        avail = ", ".join(available_sectors()) or "none found"
        raise FileNotFoundError(
            f"No sector config for '{sector}' in {SECTORS_DIR}. "
            f"Available sectors: {avail}. "
            f"Create sectors/{sector}.yaml to add one."
        )
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    companies = data.get("companies") or {}
    # Normalize: ensure CIK overrides stay zero-padded 10-digit strings (YAML may
    # parse an unquoted leading-zero value oddly; we coerce defensively).
    for ticker, meta in companies.items():
        if meta is None:
            companies[ticker] = {}
            continue
        cik = meta.get("cik")
        if cik is not None:
            meta["cik"] = str(cik).zfill(10)

    return {
        "name": data.get("name", sector.title()),
        "description": data.get("description", ""),
        "companies": companies,
    }
