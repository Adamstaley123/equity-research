"""
Unit tests for the pure computation layer (pipeline/compute.py).

These exercise the ratio math and — more importantly — the edge-case guards
that keep misleading numbers off the dashboard (negative EV, net loss,
impossible gross margins, etc.). No network or I/O.
"""
import pytest

from pipeline import compute as C
from pipeline.schema import DataPoint, ComputedRatio


def dp(value=None, na_reason=None):
    """Tiny DataPoint factory for tests."""
    return DataPoint(value=value, na_reason=na_reason)


# ── FCF ───────────────────────────────────────────────────────────────────────

def test_fcf_basic():
    r = C.compute_fcf(dp(1000.0), dp(300.0))
    assert r.value == 700.0


def test_fcf_missing_capex_treated_as_zero():
    r = C.compute_fcf(dp(1000.0), dp(None))
    assert r.value == 1000.0


def test_fcf_missing_ocf_is_na_with_reason():
    r = C.compute_fcf(dp(None, na_reason="OCF not tagged"), dp(300.0))
    assert r.value is None
    assert "OCF" in r.na_reason


# ── EV multiples + guards ─────────────────────────────────────────────────────

def test_ev_revenue_basic():
    r = C.compute_ev_revenue(dp(10_000.0), dp(2_000.0))
    assert r.value == pytest.approx(5.0)


def test_negative_ev_blocks_ev_revenue():
    r = C.compute_ev_revenue(dp(-500.0), dp(2_000.0))
    assert r.value is None
    assert "Negative EV" in r.na_reason


def test_negative_ebitda_blocks_ev_ebitda():
    r = C.compute_ev_ebitda(dp(10_000.0), dp(-50.0))
    assert r.value is None
    assert "Negative EBITDA" in r.na_reason


def test_ev_fcf_requires_positive_fcf():
    fcf = ComputedRatio(formula="OCF - CapEx", value=-100.0)
    r = C.compute_ev_fcf(dp(10_000.0), fcf)
    assert r.value is None
    assert "FCF not positive" in r.na_reason


# ── P/E guard ─────────────────────────────────────────────────────────────────

def test_pe_ttm_basic():
    r = C.compute_pe_ttm(dp(1_000.0), dp(50.0))
    assert r.value == pytest.approx(20.0)


def test_net_loss_blocks_pe():
    r = C.compute_pe_ttm(dp(1_000.0), dp(-25.0))
    assert r.value is None
    assert "Net loss" in r.na_reason


# ── Gross margin safety net (the >100% bug) ───────────────────────────────────

def test_gross_margin_basic_is_percent():
    r = C.compute_gross_margin(dp(600.0), dp(1_000.0))
    assert r.value == pytest.approx(60.0)


def test_gross_margin_over_100_rejected():
    # Gross profit > revenue (inconsistent sources) must not show an impossible figure.
    r = C.compute_gross_margin(dp(1_200.0), dp(1_000.0))
    assert r.value is None
    assert "inconsistent" in r.na_reason.lower()


def test_gross_margin_negative_rejected():
    r = C.compute_gross_margin(dp(-50.0), dp(1_000.0))
    assert r.value is None


# ── Growth, net debt, rule of 40 ──────────────────────────────────────────────

def test_revenue_growth_yoy():
    r = C.compute_revenue_growth_yoy(dp(1_100.0), dp(1_000.0))
    assert r.value == pytest.approx(10.0)


def test_net_debt_basic():
    r = C.compute_net_debt(dp(500.0), dp(200.0))
    assert r.value == pytest.approx(300.0)


def test_rule_of_40_sums_growth_and_fcf_margin():
    growth = ComputedRatio(formula="g", value=25.0)
    fcf_margin = ComputedRatio(formula="m", value=20.0)
    r = C.compute_rule_of_40(growth, fcf_margin)
    assert r.value == pytest.approx(45.0)
