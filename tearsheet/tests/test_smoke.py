"""
End-to-end smoke test: load a saved dataset and render the dashboard with NO
network access. Guards against template/renderer regressions (Jinja errors,
missing filters, broken summary stats).
"""
import os
import json

from pipeline.schema import SectorDataset, DataPoint
from pipeline.render import render, build_summary, friendly_source

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "payments_dataset.json")


def _load() -> SectorDataset:
    with open(FIXTURE) as f:
        return SectorDataset.model_validate_json(f.read())


def test_fixture_loads():
    ds = _load()
    assert ds.sector == "payments"
    assert len(ds.companies) == 13


def test_build_summary_is_robust():
    summary = build_summary(_load())
    assert summary["count"] == 13
    # Medians should be real numbers for a populated sector
    assert summary["median_ev_revenue"] is not None
    # cheapest/priciest are (ticker, value) tuples with positive multiples
    cheap = summary["cheapest_ev_ebitda"]
    assert cheap is not None and cheap[1] > 0


def test_render_produces_valid_html(tmp_path):
    out = tmp_path / "dash.html"
    render(_load(), str(out))
    html = out.read_text()

    # No unrendered Jinja tokens leaked through
    assert "{{" not in html and "{%" not in html
    # Core structure present
    assert "<table" in html and "Sector Tearsheet" in html
    assert 'class="summary"' in html
    # Every ticker rendered a row + a detail row
    ds = _load()
    for ticker in ds.companies:
        assert f'id="detail-{ticker}"' in html


def test_no_impossible_gross_margins_in_fixture():
    """The hardened pipeline must never emit a gross margin outside 0-100%."""
    ds = _load()
    for ticker, c in ds.companies.items():
        gm = c.gross_margin_pct.value
        if gm is not None:
            assert 0 <= gm <= 100, f"{ticker} gross margin {gm}% out of range"


def test_friendly_source_builds_real_sec_url():
    """EDGAR datapoints should produce a clean label + a valid sec.gov filing URL
    derived from the accession; raw provenance is preserved for the tooltip."""
    dp = DataPoint(
        value=6.15e9, source="edgar_10q",
        source_detail="edgar_10q:accessions=['0001650164-26-000114', '0001650164-25-000341']...,concept=RevenueFromContractWithCustomerExcludingAssessedTax",
        period_label="TTM",
    )
    label, url, raw = friendly_source(dp, cik="0001650164", ticker="TOST")
    assert label.startswith("SEC 10-Q")
    assert "RevenueFromContract" in label              # concept surfaced
    assert url == "https://www.sec.gov/Archives/edgar/data/1650164/000165016426000114/"
    assert raw == dp.source_detail                     # full provenance kept


def test_friendly_source_yahoo_and_computed():
    yf = DataPoint(value=1.0, source="yfinance", source_detail="yfinance:ticker.info.currentPrice (NVDA)")
    label, url, _ = friendly_source(yf, cik=None, ticker="NVDA")
    assert label == "Yahoo Finance" and url == "https://finance.yahoo.com/quote/NVDA"

    comp = DataPoint(value=1.0, source="computed", source_detail="Computed: MarketCap + Debt - Cash")
    label, url, _ = friendly_source(comp, cik="0001045810", ticker="NVDA")
    assert label == "Computed" and url is None         # no external link for computed values


# ── Sector configs & ad-hoc run-path ──────────────────────────────────────────

def test_all_sector_configs_load():
    """Every shipped sectors/*.yaml parses and defines at least one company."""
    from pipeline.sectors import available_sectors, load_sector
    sectors = available_sectors()
    assert {"payments", "semiconductors", "consumer_staples"} <= set(sectors)
    for s in sectors:
        cfg = load_sector(s)
        assert cfg["companies"], f"sector {s} has no companies"
    staples = load_sector("consumer_staples")["companies"]
    assert {"PG", "KO", "MO"} <= set(staples)


def test_slug_for_adhoc_sectors():
    from pipeline.run import _slug
    assert _slug("Utilities") == "utilities"
    assert _slug("Consumer Staples") == "consumer_staples"
    assert _slug("  My/Weird  Name! ") == "my_weird_name"
    assert _slug("") == "custom"


# ── TTM assembly regression guards (the consumer-staples bugs) ─────────────────

def test_ttm_annual_fallback_uses_full_year_not_stray_quarter():
    """GIS-class bug: a 10-K carries both a 90-day quarter AND the true full-year
    value under the same concept. The annual fallback must pick the ~365-day
    value, never the stray quarter (which produced a bogus 7.9% gross margin)."""
    from pipeline.fetchers.edgar_fetcher import _extract_ttm_quarters
    concept_data = {"units": {"USD": [
        {"form": "10-K", "val": 1_474_000_000, "start": "2025-02-24", "end": "2025-05-25", "accn": "x", "filed": "2025-07-01"},
        {"form": "10-K", "val": 6_733_000_000, "start": "2024-05-27", "end": "2025-05-25", "accn": "x", "filed": "2025-07-01"},
    ]}}
    val, label, _, _ = _extract_ttm_quarters(concept_data, "0000040704", "GrossProfit")
    assert val == 6_733_000_000
    assert label.startswith("FY")


def test_ttm_sums_four_contiguous_quarters():
    from pipeline.fetchers.edgar_fetcher import _extract_ttm_quarters
    qs = [("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30"),
          ("2025-10-01", "2025-12-31"), ("2026-01-01", "2026-03-31")]
    entries = [{"form": "10-Q", "val": 100, "start": s, "end": e, "accn": "a", "filed": e} for s, e in qs]
    val, _, end, _ = _extract_ttm_quarters({"units": {"USD": entries}}, "x", "Revenues")
    assert val == 400 and end == "2026-03-31"


def test_ttm_rejects_quarter_skip():
    """PEP-class bug: four single quarters that SKIP a period (span >> coverage)
    must NOT be naively summed. With no YTD/annual data to fall back on, return
    N/A rather than a silently-wrong number."""
    from pipeline.fetchers.edgar_fetcher import _extract_ttm_quarters
    qs = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
          ("2025-07-01", "2025-09-30"), ("2026-01-01", "2026-03-31")]   # skips Q4 2025
    entries = [{"form": "10-K", "val": 100, "start": s, "end": e, "accn": "a", "filed": e} for s, e in qs]
    val, _, _, _ = _extract_ttm_quarters({"units": {"USD": entries}}, "x", "Revenues")
    assert val is None
