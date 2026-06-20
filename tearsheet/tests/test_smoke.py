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


# ── Combined multi-sector dashboard ───────────────────────────────────────────

def test_cap_bucket_boundaries():
    """Market-cap buckets must match the bounds the template's JS uses."""
    from pipeline.combined import cap_bucket
    assert cap_bucket(250e9) == "mega"
    assert cap_bucket(200e9) == "mega"     # boundary is inclusive at the low end
    assert cap_bucket(199e9) == "large"
    assert cap_bucket(10e9) == "large"
    assert cap_bucket(9.9e9) == "mid"
    assert cap_bucket(2e9) == "mid"
    assert cap_bucket(1.9e9) == "small"
    assert cap_bucket(0.0) == "small"
    assert cap_bucket(None) is None


def test_combined_loads_committed_datasets():
    """The built-in sectors each have a committed stable dataset JSON, and the
    combined summary counts every company across them."""
    from pipeline.combined import load_datasets, combined_summary, DEFAULT_SECTORS
    datasets = load_datasets()
    assert {d.sector for d in datasets} == set(DEFAULT_SECTORS)
    total = sum(len([t for t in d.tickers if t in d.companies]) for d in datasets)
    assert combined_summary(datasets)["count"] == total


def test_combined_render_is_valid_and_complete(tmp_path):
    from pipeline.combined import load_datasets, render_combined
    datasets = load_datasets()
    out = tmp_path / "combined.html"
    render_combined(datasets, str(out))
    html = out.read_text()

    # No unrendered Jinja, both filter controls present
    assert "{{" not in html and "{%" not in html
    assert 'id="sectorFilter"' in html and 'id="capFilter"' in html
    # Every company across every sector rendered a row + detail row, tagged by sector
    for ds in datasets:
        assert f'data-sector="{ds.sector}"' in html
        for ticker in ds.tickers:
            assert f'data-ticker="{ticker}"' in html
            assert f'id="detail-{ticker}"' in html


def test_combined_render_is_deterministic(tmp_path):
    """Same committed JSONs in → byte-identical HTML out (no network, no clock)."""
    from pipeline.combined import load_datasets, render_combined
    datasets = load_datasets()
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    render_combined(datasets, str(a))
    render_combined(datasets, str(b))
    assert a.read_bytes() == b.read_bytes()


def test_revenue_annual_pair_prefers_freshest_concept(monkeypatch):
    """NVDA-class bug: a company ABANDONS one revenue XBRL concept for another but
    the old concept still carries its last few years. The annual pair (used for
    YoY growth) must follow the FRESHEST concept, not the first one in the list
    that happens to have two annuals — else growth is computed from 3-year-stale
    figures (NVIDIA showed 61% off FY2021–FY2023 instead of 65% off FY2025→FY2026)."""
    from pipeline.fetchers import edgar_fetcher as EF
    abandoned = {"units": {"USD": [
        {"form": "10-K", "val": 16_675_000_000, "start": "2020-01-27", "end": "2021-01-31", "accn": "old1", "filed": "2021-02-26"},
        {"form": "10-K", "val": 26_914_000_000, "start": "2021-02-01", "end": "2022-01-30", "accn": "old2", "filed": "2022-03-18"},
        {"form": "10-K", "val": 26_974_000_000, "start": "2022-01-31", "end": "2023-01-29", "accn": "old3", "filed": "2023-02-24"},
    ]}}
    current = {"units": {"USD": [
        {"form": "10-K", "val": 130_497_000_000, "start": "2024-01-29", "end": "2025-01-26", "accn": "new1", "filed": "2025-02-26"},
        {"form": "10-K", "val": 215_938_000_000, "start": "2025-01-27", "end": "2026-01-25", "accn": "new2", "filed": "2026-02-25"},
    ]}}

    def fake_concept(cik, concept):
        if concept == "RevenueFromContractWithCustomerExcludingAssessedTax":
            return abandoned
        if concept == "Revenues":
            return current
        return None

    monkeypatch.setattr(EF, "_fetch_concept", fake_concept)
    cur, prior = EF.fetch_revenue_annual_pair("0001045810")
    assert round(cur.value / 1e9) == 216 and round(prior.value / 1e9) == 130
    assert "concept=Revenues" in cur.source_detail   # followed the fresh concept
    growth = (cur.value - prior.value) / prior.value * 100
    assert 64 < growth < 67


# ── Concurrency safety (parallel per-company fetch) ───────────────────────────

def test_edgar_throttle_stays_under_rate_limit_with_many_workers():
    """The pipeline fetches companies concurrently, so EDGAR request *initiations*
    must be spaced by the global throttle no matter the worker count — otherwise
    N workers would breach SEC's 10 req/sec cap. 12 throttled calls across 6
    workers must take at least ~(12-1)×interval, proving they serialized."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pipeline.fetchers import edgar_fetcher as EF
    from pipeline.config import EDGAR_RATE_LIMIT_SLEEP

    EF._last_request_t = 0.0
    n = 12
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: EF._throttle(), range(n)))
    elapsed = time.monotonic() - t0
    assert elapsed >= (n - 1) * EDGAR_RATE_LIMIT_SLEEP * 0.9


def test_dataset_assembly_preserves_ticker_order():
    """Companies complete out of order under the thread pool, but the dataset is
    assembled in the original --tickers order (this is the invariant run.py relies
    on so concurrent output matches the old sequential output)."""
    from pipeline.schema import SectorDataset, CompanyRecord
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    # Simulate completion in a scrambled order:
    companies = {}
    for t in ["CCC", "AAA", "DDD", "BBB"]:
        companies[t] = CompanyRecord(ticker=t, company_name=t, sector="x", run_id="r")
    ds = SectorDataset(
        sector="x", run_id="r", generated_at="2026-06-20",
        tickers=[t for t in tickers if t in companies], companies=companies,
    )
    assert ds.tickers == tickers
