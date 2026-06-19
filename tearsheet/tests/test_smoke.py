"""
End-to-end smoke test: load a saved dataset and render the dashboard with NO
network access. Guards against template/renderer regressions (Jinja errors,
missing filters, broken summary stats).
"""
import os
import json

from pipeline.schema import SectorDataset
from pipeline.render import render, build_summary

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
