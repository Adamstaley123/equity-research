"""
Pure computation layer — no I/O, no external calls.

Contract:
  - All inputs are DataPoint or ComputedRatio.
  - If any required input has value=None, the result is a ComputedRatio with
    value=None and na_reason quoting the upstream na_reason.
  - Edge-case guards: negative EV, negative EBITDA, net loss, etc.
  - inputs dict is always populated even when the ratio is N/A (tooltip shows raw numbers).
"""
from __future__ import annotations
from typing import Optional, Union
from pipeline.schema import DataPoint, ComputedRatio


def _val(dp: Union[DataPoint, ComputedRatio]) -> Optional[float]:
    return dp.value


def _reason(dp: Union[DataPoint, ComputedRatio], name: str) -> str:
    if isinstance(dp, DataPoint):
        return dp.na_reason or f"{name} is None (no reason recorded)"
    return dp.na_reason or f"{name} ratio is None"


def _fmt(v: Optional[float], suffix: str = "") -> str:
    """Format a raw value for display in input_labels."""
    if v is None:
        return "N/A"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B{suffix}"
    if abs(v) >= 1e6:
        return f"${v/1e6:.1f}M{suffix}"
    if abs(v) >= 1e3:
        return f"${v/1e3:.1f}K{suffix}"
    return f"{v:.2f}{suffix}"


def _ratio(
    formula: str,
    numerator: Optional[float],
    denominator: Optional[float],
    inputs: dict,
    input_labels: dict,
    na_reason: str = "",
    guard: Optional[str] = None,
) -> ComputedRatio:
    if na_reason:
        return ComputedRatio(formula=formula, value=None, inputs=inputs, input_labels=input_labels, na_reason=na_reason)
    if guard:
        return ComputedRatio(formula=formula, value=None, inputs=inputs, input_labels=input_labels, na_reason=guard)
    if numerator is None or denominator is None or denominator == 0:
        return ComputedRatio(
            formula=formula, value=None, inputs=inputs, input_labels=input_labels,
            na_reason="Denominator is zero or a required input is None",
        )
    return ComputedRatio(
        formula=formula,
        value=numerator / denominator,
        inputs=inputs,
        input_labels=input_labels,
    )


# ── Individual compute functions ──────────────────────────────────────────────

def compute_fcf(ocf: DataPoint, capex: DataPoint) -> ComputedRatio:
    o, c = _val(ocf), _val(capex)
    inputs = {"OCF": o, "CapEx": c}
    labels = {"OCF": _fmt(o), "CapEx": _fmt(c)}
    if o is None:
        return ComputedRatio(formula="OCF - CapEx", value=None, inputs=inputs, input_labels=labels,
                             na_reason=f"OCF unavailable: {_reason(ocf, 'OCF')}")
    capex_val = c if c is not None else 0.0
    return ComputedRatio(formula="OCF - CapEx", value=o - capex_val, inputs=inputs, input_labels=labels)


def compute_ev_revenue(ev: DataPoint, revenue: DataPoint) -> ComputedRatio:
    e, r = _val(ev), _val(revenue)
    inputs = {"EV": e, "Revenue_TTM": r}
    labels = {"EV": _fmt(e), "Revenue_TTM": _fmt(r)}
    guard = None
    if e is not None and e < 0:
        guard = f"Negative EV ({_fmt(e)}) — EV multiples not meaningful"
    na = "" if (e is not None and r is not None) else (
        _reason(ev, "EV") if e is None else _reason(revenue, "Revenue")
    )
    return _ratio("EV / Revenue_TTM", e, r, inputs, labels, na_reason=na, guard=guard)


def compute_ev_ebitda(ev: DataPoint, ebitda: DataPoint) -> ComputedRatio:
    e, b = _val(ev), _val(ebitda)
    inputs = {"EV": e, "EBITDA": b}
    labels = {"EV": _fmt(e), "EBITDA": _fmt(b)}
    guard = None
    if e is not None and e < 0:
        guard = f"Negative EV ({_fmt(e)}) — EV multiples not meaningful"
    elif b is not None and b < 0:
        guard = f"Negative EBITDA ({_fmt(b)}) — EV/EBITDA not meaningful"
    na = "" if (e is not None and b is not None) else (
        _reason(ev, "EV") if e is None else _reason(ebitda, "EBITDA")
    )
    return _ratio("EV / EBITDA", e, b, inputs, labels, na_reason=na, guard=guard)


def compute_ev_fcf(ev: DataPoint, fcf: ComputedRatio) -> ComputedRatio:
    e, f = _val(ev), _val(fcf)
    inputs = {"EV": e, "FCF": f}
    labels = {"EV": _fmt(e), "FCF": _fmt(f)}
    guard = None
    if e is not None and e < 0:
        guard = f"Negative EV ({_fmt(e)}) — EV multiples not meaningful"
    elif f is not None and f <= 0:
        guard = f"FCF not positive ({_fmt(f)}) — EV/FCF not meaningful"
    na = "" if (e is not None and f is not None) else (
        _reason(ev, "EV") if e is None else (fcf.na_reason or "FCF is None")
    )
    return _ratio("EV / FCF", e, f, inputs, labels, na_reason=na, guard=guard)


def compute_pe_ttm(market_cap: DataPoint, net_income: DataPoint) -> ComputedRatio:
    m, n = _val(market_cap), _val(net_income)
    inputs = {"MarketCap": m, "NetIncome_TTM": n}
    labels = {"MarketCap": _fmt(m), "NetIncome_TTM": _fmt(n)}
    guard = None
    if n is not None and n <= 0:
        guard = f"Net loss TTM ({_fmt(n)}) — P/E not meaningful"
    na = "" if (m is not None and n is not None) else (
        _reason(market_cap, "MarketCap") if m is None else _reason(net_income, "NetIncome")
    )
    return _ratio("MarketCap / NetIncome_TTM", m, n, inputs, labels, na_reason=na, guard=guard)


def compute_pe_forward(price: DataPoint, eps_forward: DataPoint) -> ComputedRatio:
    p, e = _val(price), _val(eps_forward)
    inputs = {"Price": p, "EPS_Forward": e}
    labels = {"Price": _fmt(p), "EPS_Forward": f"${e:.2f}" if e is not None else "N/A"}
    guard = None
    if e is not None and e <= 0:
        guard = f"Forward EPS not positive ({e:.2f}) — Forward P/E not meaningful"
    na = "" if (p is not None and e is not None) else (
        _reason(price, "Price") if p is None else _reason(eps_forward, "EPS_Forward")
    )
    return _ratio("Price / EPS_Forward", p, e, inputs, labels, na_reason=na, guard=guard)


def compute_peg_ratio(pe_forward: ComputedRatio, eps_growth: DataPoint) -> ComputedRatio:
    pe, g = _val(pe_forward), _val(eps_growth)
    inputs = {"ForwardPE": pe, "EPSGrowthPct": g}
    labels = {
        "ForwardPE": f"{pe:.1f}x" if pe is not None else "N/A",
        "EPSGrowthPct": f"{g:.1f}%" if g is not None else "N/A (analyst estimate)",
    }
    guard = None
    if pe is None:
        guard = pe_forward.na_reason or "Forward P/E unavailable"
    elif g is None or g <= 0:
        guard = "EPS growth estimate unavailable or negative — PEG not meaningful"
    na = ""
    return _ratio("ForwardPE / EPSGrowthPct", pe, g, inputs, labels, na_reason=na, guard=guard)


def compute_price_sales(market_cap: DataPoint, revenue: DataPoint) -> ComputedRatio:
    m, r = _val(market_cap), _val(revenue)
    inputs = {"MarketCap": m, "Revenue_TTM": r}
    labels = {"MarketCap": _fmt(m), "Revenue_TTM": _fmt(r)}
    na = "" if (m is not None and r is not None) else (
        _reason(market_cap, "MarketCap") if m is None else _reason(revenue, "Revenue")
    )
    return _ratio("MarketCap / Revenue_TTM", m, r, inputs, labels, na_reason=na)


def compute_price_fcf(market_cap: DataPoint, fcf: ComputedRatio) -> ComputedRatio:
    m, f = _val(market_cap), _val(fcf)
    inputs = {"MarketCap": m, "FCF": f}
    labels = {"MarketCap": _fmt(m), "FCF": _fmt(f)}
    guard = None
    if f is not None and f <= 0:
        guard = f"FCF not positive ({_fmt(f)}) — Price/FCF not meaningful"
    na = "" if (m is not None and f is not None) else (
        _reason(market_cap, "MarketCap") if m is None else (fcf.na_reason or "FCF is None")
    )
    return _ratio("MarketCap / FCF", m, f, inputs, labels, na_reason=na, guard=guard)


def compute_gross_margin(gross_profit: DataPoint, revenue: DataPoint) -> ComputedRatio:
    g, r = _val(gross_profit), _val(revenue)
    inputs = {"GrossProfit": g, "Revenue_TTM": r}
    labels = {"GrossProfit": _fmt(g), "Revenue_TTM": _fmt(r)}
    na = "" if (g is not None and r is not None) else (
        _reason(gross_profit, "GrossProfit") if g is None else _reason(revenue, "Revenue")
    )
    ratio = _ratio("GrossProfit / Revenue_TTM", g, r, inputs, labels, na_reason=na)
    if ratio.value is not None:
        pct = ratio.value * 100
        # Safety net: a gross margin outside [0, 100] means gross profit and revenue
        # came from inconsistent bases — reject rather than display an impossible figure.
        if pct < 0 or pct > 100:
            return ratio.model_copy(update={
                "value": None,
                "na_reason": (
                    f"Computed gross margin {pct:.0f}% is outside 0–100% — gross profit "
                    f"and revenue are on inconsistent bases; not shown"
                ),
            })
        ratio = ratio.model_copy(update={"value": pct})
    return ratio


def compute_ebitda_margin(ebitda: DataPoint, revenue: DataPoint) -> ComputedRatio:
    b, r = _val(ebitda), _val(revenue)
    inputs = {"EBITDA": b, "Revenue_TTM": r}
    labels = {"EBITDA": _fmt(b), "Revenue_TTM": _fmt(r)}
    na = "" if (b is not None and r is not None) else (
        _reason(ebitda, "EBITDA") if b is None else _reason(revenue, "Revenue")
    )
    ratio = _ratio("EBITDA / Revenue_TTM", b, r, inputs, labels, na_reason=na)
    if ratio.value is not None:
        ratio = ratio.model_copy(update={"value": ratio.value * 100})
    return ratio


def compute_fcf_margin(fcf: ComputedRatio, revenue: DataPoint) -> ComputedRatio:
    f, r = _val(fcf), _val(revenue)
    inputs = {"FCF": f, "Revenue_TTM": r}
    labels = {"FCF": _fmt(f), "Revenue_TTM": _fmt(r)}
    na = "" if (f is not None and r is not None) else (
        fcf.na_reason or "FCF is None" if f is None else _reason(revenue, "Revenue")
    )
    ratio = _ratio("FCF / Revenue_TTM", f, r, inputs, labels, na_reason=na)
    if ratio.value is not None:
        ratio = ratio.model_copy(update={"value": ratio.value * 100})
    return ratio


def compute_revenue_growth_yoy(revenue_ttm: DataPoint, revenue_prior: DataPoint) -> ComputedRatio:
    c, p = _val(revenue_ttm), _val(revenue_prior)
    inputs = {"Revenue_TTM": c, "Revenue_PriorYear": p}
    labels = {"Revenue_TTM": _fmt(c), "Revenue_PriorYear": _fmt(p)}
    na = "" if (c is not None and p is not None) else (
        _reason(revenue_ttm, "Revenue_TTM") if c is None else _reason(revenue_prior, "Revenue_PriorYear")
    )
    if na or p is None or p == 0:
        return ComputedRatio(formula="(Revenue_TTM - Revenue_PriorYear) / Revenue_PriorYear",
                             value=None, inputs=inputs, input_labels=labels, na_reason=na or "Prior year revenue is zero")
    val = (c - p) / p * 100
    return ComputedRatio(formula="(Revenue_TTM - Revenue_PriorYear) / Revenue_PriorYear",
                         value=val, inputs=inputs, input_labels=labels)


def compute_net_debt(total_debt: DataPoint, total_cash: DataPoint) -> ComputedRatio:
    d, c = _val(total_debt), _val(total_cash)
    inputs = {"TotalDebt": d, "TotalCash": c}
    labels = {"TotalDebt": _fmt(d), "TotalCash": _fmt(c)}
    if d is None and c is None:
        return ComputedRatio(formula="TotalDebt - TotalCash", value=None, inputs=inputs, input_labels=labels,
                             na_reason="Both debt and cash unavailable")
    debt_val = d if d is not None else 0.0
    cash_val = c if c is not None else 0.0
    return ComputedRatio(formula="TotalDebt - TotalCash", value=debt_val - cash_val, inputs=inputs, input_labels=labels)


def compute_net_debt_ebitda(net_debt: ComputedRatio, ebitda: DataPoint) -> ComputedRatio:
    nd, b = _val(net_debt), _val(ebitda)
    inputs = {"NetDebt": nd, "EBITDA": b}
    labels = {"NetDebt": _fmt(nd), "EBITDA": _fmt(b)}
    guard = None
    if nd is not None and nd < 0:
        guard = f"Net cash position ({_fmt(nd)}) — Net Debt/EBITDA not meaningful; company has more cash than debt"
    if b is not None and b <= 0:
        guard = f"EBITDA not positive ({_fmt(b)}) — leverage ratio not meaningful"
    na = "" if (nd is not None and b is not None) else (
        net_debt.na_reason or "NetDebt is None" if nd is None else _reason(ebitda, "EBITDA")
    )
    return _ratio("NetDebt / EBITDA", nd, b, inputs, labels, na_reason=na, guard=guard)


def compute_rule_of_40(revenue_growth: ComputedRatio, fcf_margin: ComputedRatio) -> ComputedRatio:
    g, f = _val(revenue_growth), _val(fcf_margin)
    inputs = {"RevGrowthPct": g, "FCFMarginPct": f}
    labels = {
        "RevGrowthPct": f"{g:.1f}%" if g is not None else "N/A",
        "FCFMarginPct": f"{f:.1f}%" if f is not None else "N/A",
    }
    if g is None or f is None:
        return ComputedRatio(
            formula="RevGrowthPct + FCFMarginPct", value=None,
            inputs=inputs, input_labels=labels,
            na_reason=(revenue_growth.na_reason or "RevGrowth is None") if g is None else
                      (fcf_margin.na_reason or "FCFMargin is None"),
        )
    return ComputedRatio(formula="RevGrowthPct + FCFMarginPct", value=g + f, inputs=inputs, input_labels=labels)
