"""
Build the combined multi-sector dashboard.

This is a thin CLI over `pipeline.combined`. It reads the committed per-sector
dataset JSONs (written by `run.py` to `examples/data/<sector>_dataset.json`) and
renders ONE HTML page with a sector dropdown + market-cap filter. It performs no
network I/O — it is a deterministic render over already-fetched, verified data.

Usage:
  python pipeline/build_combined.py
  python pipeline/build_combined.py --sectors payments,semiconductors --out docs/index.html
"""
from __future__ import annotations
import os
import sys
from typing import Optional

import typer

# Ensure project root is on sys.path when run from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.combined import DEFAULT_SECTORS, load_datasets, render_combined  # noqa: E402

app = typer.Typer(add_completion=False)

# Repo root is two levels above this file's package (…/equity-research).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_OUT = os.path.join(_REPO_ROOT, "docs", "index.html")


@app.command()
def main(
    sectors: Optional[str] = typer.Option(
        None, help="Comma-separated sectors to include (default: the built-ins "
                   f"{', '.join(DEFAULT_SECTORS)}). Each must have a committed "
                   "examples/data/<sector>_dataset.json."),
    out: str = typer.Option(
        _DEFAULT_OUT, help="Output HTML path (default: repo docs/index.html — the "
                           "hosted GitHub Pages landing page)."),
):
    sector_list = (
        [s.strip() for s in sectors.split(",") if s.strip()] if sectors else DEFAULT_SECTORS
    )
    datasets = load_datasets(sector_list)
    found = {d.sector for d in datasets}
    missing = [s for s in sector_list if s not in found]
    if missing:
        typer.echo(
            f"⚠  No committed dataset for: {', '.join(missing)} — skipped. "
            f"Run `python pipeline/run.py --sector <name>` to generate it.",
            err=True,
        )
    render_combined(datasets, out)
    n_companies = sum(len([t for t in d.tickers if t in d.companies]) for d in datasets)
    typer.echo(
        f"✅ Combined dashboard → {out}\n"
        f"   {len(datasets)} sector(s), {n_companies} companies: "
        f"{', '.join(d.sector for d in datasets)}"
    )


if __name__ == "__main__":
    app()
