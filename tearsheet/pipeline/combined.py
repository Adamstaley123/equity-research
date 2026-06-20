"""
Combined multi-sector dashboard.

This renders ONE HTML page spanning several curated sectors, with a sector
dropdown and a market-cap bucket filter. It is a **pure function of the committed
per-sector dataset JSONs** — it does no network I/O and runs no model. Same JSONs
in → same HTML out. The single-sector pipeline (`run.py`) writes those stable
JSONs to `examples/data/<sector>_dataset.json`; this module reads them back.

The per-row markup is the exact same Jinja macro the single-sector dashboard uses
(`templates/_company_rows.html.j2`), so the two views can never drift apart.
"""
from __future__ import annotations
import os
import statistics
from typing import Optional

from pipeline.schema import SectorDataset
from pipeline.render import _make_env, build_summary

# Stable, committed dataset JSONs live alongside the curated HTML examples.
STABLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "data"
)

# The built-in curated sectors, in display order.
DEFAULT_SECTORS = ["payments", "semiconductors", "consumer_staples"]

# Market-cap buckets (USD). Mirrored in the template's JS — keep in sync.
# (label, lower_inclusive, upper_exclusive)
CAP_BUCKETS = [
    ("mega", 200e9, float("inf")),
    ("large", 10e9, 200e9),
    ("mid", 2e9, 10e9),
    ("small", 0.0, 2e9),
]


def cap_bucket(market_cap: Optional[float]) -> Optional[str]:
    """Return the bucket key ('mega'|'large'|'mid'|'small') for a market cap in
    dollars, or None if unknown. The template's JS applies the identical bounds."""
    if market_cap is None:
        return None
    for label, lo, hi in CAP_BUCKETS:
        if lo <= market_cap < hi:
            return label
    return None


def dataset_path(sector: str, stable_dir: str = STABLE_DIR) -> str:
    return os.path.join(stable_dir, f"{sector}_dataset.json")


def load_datasets(
    sectors: Optional[list[str]] = None, stable_dir: str = STABLE_DIR
) -> list[SectorDataset]:
    """Load each sector's stable dataset JSON. Missing files are skipped with a
    note so a partial set still renders (e.g. before every sector is generated)."""
    sectors = sectors or DEFAULT_SECTORS
    out: list[SectorDataset] = []
    for s in sectors:
        path = dataset_path(s, stable_dir)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            out.append(SectorDataset.model_validate_json(f.read()))
    return out


def combined_summary(datasets: list[SectorDataset]) -> dict:
    """Headline aggregates across ALL companies in every sector — the initial
    ('All / All') summary shown on load. The template recomputes this client-side
    as the user filters, but rendering it server-side keeps the page correct with
    JS disabled."""
    all_records = [
        c for ds in datasets for t in ds.tickers if t in ds.companies
        for c in [ds.companies[t]]
    ]

    def vals(attr: str) -> list[float]:
        return [getattr(c, attr).value for c in all_records if getattr(c, attr).value is not None]

    def median(xs: list[float]) -> Optional[float]:
        return statistics.median(xs) if xs else None

    ev_ebitda_pairs = [(c.ticker, c.ev_ebitda.value) for c in all_records if c.ev_ebitda.value is not None]
    pos = [(t, v) for t, v in ev_ebitda_pairs if v > 0]
    cheapest = min(pos, key=lambda p: p[1]) if pos else None
    priciest = max(pos, key=lambda p: p[1]) if pos else None

    return {
        "count": len(all_records),
        "median_ev_ebitda": median(vals("ev_ebitda")),
        "median_ev_revenue": median(vals("ev_revenue")),
        "median_rev_growth": median(vals("revenue_growth_yoy_pct")),
        "median_rule_of_40": median(vals("rule_of_40")),
        "cheapest_ev_ebitda": cheapest,
        "priciest_ev_ebitda": priciest,
    }


def sector_options(datasets: list[SectorDataset]) -> list[dict]:
    """Sector dropdown options: value (slug), label (Title Case), and count."""
    return [
        {
            "value": ds.sector,
            "label": ds.sector.replace("_", " ").title(),
            "count": len([t for t in ds.tickers if t in ds.companies]),
        }
        for ds in datasets
    ]


def render_combined(datasets: list[SectorDataset], output_path: str) -> None:
    if not datasets:
        raise ValueError(
            f"No sector datasets found in {STABLE_DIR}. Run the pipeline first "
            f"(e.g. `python pipeline/run.py --sector payments`) to produce them."
        )
    env = _make_env()
    template = env.get_template("combined.html.j2")
    # newest generated_at across the set, for the masthead "as of" line
    as_of = max((ds.generated_at for ds in datasets), default="")
    pipeline_version = datasets[0].pipeline_version
    html = template.render(
        datasets=datasets,
        summary=combined_summary(datasets),
        sectors=sector_options(datasets),
        as_of=as_of,
        pipeline_version=pipeline_version,
    )
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
