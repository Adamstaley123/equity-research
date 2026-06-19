"""
HTML renderer — consumes a SectorDataset and renders the Jinja2 template.

All formatting logic (color classes, tooltip text, detail cards) lives here
as Jinja2 filters so the template stays declarative.
"""
from __future__ import annotations
import os
import statistics
from typing import Optional, Union
from jinja2 import Environment, FileSystemLoader

from pipeline.schema import DataPoint, ComputedRatio, SectorDataset, CompanyRecord
from pipeline.config import (
    EV_REVENUE_THRESHOLDS, EV_EBITDA_THRESHOLDS, EV_FCF_THRESHOLDS,
    PE_TTM_THRESHOLDS, FWD_PE_THRESHOLDS, NET_DEBT_EBITDA_THRESH,
    RULE_OF_40_THRESHOLDS, GROSS_MARGIN_THRESHOLDS,
    EBITDA_MARGIN_THRESHOLDS, FCF_MARGIN_THRESHOLDS, NA_SENTINEL,
)

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# ── Color helpers ─────────────────────────────────────────────────────────────

def _color_lower_is_better(value: float, green_max: float, yellow_max: float) -> str:
    if value <= green_max:
        return "g"
    if value <= yellow_max:
        return "y"
    return "r"


def _color_higher_is_better(value: float, green_min: float, yellow_min: float) -> str:
    if value >= green_min:
        return "g"
    if value >= yellow_min:
        return "y"
    return "r"


_THRESHOLD_MAP = {
    "ev_revenue":       (EV_REVENUE_THRESHOLDS,   "lower"),
    "ev_ebitda":        (EV_EBITDA_THRESHOLDS,    "lower"),
    "ev_fcf":           (EV_FCF_THRESHOLDS,       "lower"),
    "pe_ttm":           (PE_TTM_THRESHOLDS,       "lower"),
    "fwd_pe":           (FWD_PE_THRESHOLDS,       "lower"),
    "price_fcf":        (EV_FCF_THRESHOLDS,       "lower"),
    "net_debt_ebitda":  (NET_DEBT_EBITDA_THRESH,  "lower"),
    "rule_of_40":       (RULE_OF_40_THRESHOLDS,   "higher"),
    "gross_margin":     (GROSS_MARGIN_THRESHOLDS,  "higher"),
    "ebitda_margin":    (EBITDA_MARGIN_THRESHOLDS, "higher"),
    "fcf_margin":       (FCF_MARGIN_THRESHOLDS,    "higher"),
}


def color_class_filter(ratio: ComputedRatio, metric: str) -> str:
    if ratio.value is None:
        return ""
    thresholds, direction = _THRESHOLD_MAP.get(metric, (None, None))
    if thresholds is None:
        return ""
    if direction == "lower":
        return _color_lower_is_better(ratio.value, *thresholds)
    return _color_higher_is_better(ratio.value, *thresholds)


# ── Number formatters ─────────────────────────────────────────────────────────

def fmt_num(value: Optional[float]) -> str:
    """Return float for data-* attribute (sentinel for None)."""
    if value is None:
        return str(NA_SENTINEL)
    return f"{value:.4f}"


def fmt_rev(value: Optional[float]) -> str:
    """Revenue in billions for data-* attribute."""
    if value is None:
        return str(NA_SENTINEL)
    return f"{value / 1e9:.4f}"


def fmt_b_display(value: Optional[float]) -> str:
    """Format as $X.XXB for display in table cells."""
    if value is None:
        return '<span class="na">N/A</span>'
    b = value / 1e9
    if abs(b) >= 10:
        return f"${b:.1f}B"
    return f"${b:.2f}B"


def fmt_ratio_x(ratio: Union[ComputedRatio, DataPoint]) -> str:
    """Format a ratio as Xx or N/A span."""
    v = ratio.value
    if v is None:
        reason = ratio.na_reason or "N/A"
        short = reason[:60] + "…" if len(reason) > 60 else reason
        return f'<span class="na" title="{_escape(short)}">N/A</span>'
    return f"{v:.2f}x"


def fmt_ratio_pct(ratio: ComputedRatio) -> str:
    """Format a ratio as X.X% or N/A span."""
    v = ratio.value
    if v is None:
        reason = ratio.na_reason or "N/A"
        short = reason[:60] + "…" if len(reason) > 60 else reason
        return f'<span class="na" title="{_escape(short)}">N/A</span>'
    return f"{v:.1f}%"


def fmt_ratio_plain(ratio: ComputedRatio) -> str:
    """Format a ratio as plain number or N/A span."""
    v = ratio.value
    if v is None:
        reason = ratio.na_reason or "N/A"
        short = reason[:60] + "…" if len(reason) > 60 else reason
        return f'<span class="na" title="{_escape(short)}">N/A</span>'
    return f"{v:.0f}"


# ── Tooltip helpers ───────────────────────────────────────────────────────────

def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def dp_tooltip(dp: DataPoint, label: str) -> str:
    parts = [label]
    if dp.value is not None:
        parts.append(f"= {dp.value / 1e9:.3f}B")
    if dp.source_detail:
        parts.append(f"Source: {dp.source_detail}")
    if dp.period_label:
        parts.append(f"Period: {dp.period_label}")
    if dp.na_reason:
        parts.append(f"N/A: {dp.na_reason[:120]}")
    return _escape(" | ".join(parts))


def ratio_tooltip(ratio: ComputedRatio) -> str:
    parts = [ratio.formula]
    if ratio.value is not None:
        parts.append(f"= {ratio.value:.3f}")
    if ratio.inputs:
        inp = ", ".join(f"{k}={v:.3g}" if v is not None else f"{k}=N/A" for k, v in ratio.inputs.items())
        parts.append(f"Inputs: {inp}")
    if ratio.na_reason:
        parts.append(f"N/A: {ratio.na_reason[:120]}")
    return _escape(" | ".join(parts))


# ── Detail card generators ────────────────────────────────────────────────────

def _fmt_raw(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.3f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"{value:,.0f}"


def detail_card(dp: DataPoint, label: str, note: str = "") -> str:
    if dp.value is not None:
        display = _fmt_raw(dp.value)
        src_html = ""
        if dp.source_detail:
            src_html = f'<div class="detail-line"><strong>Source:</strong> {_escape(dp.source_detail)}</div>'
        period_html = ""
        if dp.period_label:
            period_html = f'<div class="detail-line"><strong>Period:</strong> {_escape(dp.period_label)}</div>'
        note_html = f'<div class="detail-line">{_escape(note)}</div>' if note else ""
        return f"""
<div class="detail-card">
  <div class="detail-metric">{_escape(label)}</div>
  <div class="detail-value">{_escape(display)}</div>
  {src_html}{period_html}{note_html}
</div>"""
    else:
        reason = dp.na_reason or "No data available"
        return f"""
<div class="detail-card na-card">
  <div class="detail-metric">{_escape(label)}</div>
  <div class="detail-value na">N/A</div>
  <div class="detail-line reason"><strong>Reason:</strong> {_escape(reason)}</div>
</div>"""


def ratio_detail_card(ratio: ComputedRatio, label: str) -> str:
    if ratio.value is not None:
        v = ratio.value
        is_pct = "%" in label or "Margin" in label or "Growth" in label or "Rule of" in label
        is_currency = "Cash Flow" in label or "Cash" in label or "Debt" in label or "Profit" in label or "Income" in label or "Revenue" in label
        if is_currency and abs(v) >= 1e6:
            display = _fmt_raw(v)
        elif is_pct:
            display = f"{v:.1f}%"
        elif abs(v) >= 10:
            display = f"{v:.2f}x"
        else:
            display = f"{v:.3f}x"

        inputs_html = ""
        if ratio.input_labels:
            inp = " &nbsp;|&nbsp; ".join(
                f"<strong>{_escape(k)}</strong>: {_escape(str(vv))}"
                for k, vv in ratio.input_labels.items()
            )
            inputs_html = f'<div class="detail-line"><strong>Inputs:</strong> {inp}</div>'

        formula_html = f'<div class="detail-line"><strong>Formula:</strong> {_escape(ratio.formula)}</div>'
        return f"""
<div class="detail-card">
  <div class="detail-metric">{_escape(label)}</div>
  <div class="detail-value">{_escape(display)}</div>
  {formula_html}{inputs_html}
</div>"""
    else:
        reason = ratio.na_reason or "N/A"
        inputs_html = ""
        if ratio.input_labels:
            inp = " &nbsp;|&nbsp; ".join(
                f"<strong>{_escape(k)}</strong>: {_escape(str(vv))}"
                for k, vv in ratio.input_labels.items()
            )
            inputs_html = f'<div class="detail-line"><strong>Raw inputs (partial):</strong> {inp}</div>'
        return f"""
<div class="detail-card na-card">
  <div class="detail-metric">{_escape(label)}</div>
  <div class="detail-value na">N/A</div>
  <div class="detail-line reason"><strong>Reason:</strong> {_escape(reason)}</div>
  {inputs_html}
</div>"""


# ── Summary stats (hero band) ─────────────────────────────────────────────────

def _median(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def build_summary(dataset: SectorDataset) -> dict:
    """Compute headline aggregates for the summary band: medians and the
    cheapest/priciest names by EV/EBITDA. All robust to N/A values."""
    comps = [dataset.companies[t] for t in dataset.tickers if t in dataset.companies]

    def pairs(attr: str) -> list[tuple[str, float]]:
        out = []
        for c in comps:
            v = getattr(c, attr).value
            if v is not None:
                out.append((c.ticker, v))
        return out

    ev_ebitda = pairs("ev_ebitda")
    ev_rev = pairs("ev_revenue")
    revg = pairs("revenue_growth_yoy_pct")
    r40 = pairs("rule_of_40")

    # Cheapest/priciest by EV/EBITDA, considering only positive multiples
    pos_ev_ebitda = [(t, v) for t, v in ev_ebitda if v > 0]
    cheapest = min(pos_ev_ebitda, key=lambda p: p[1]) if pos_ev_ebitda else None
    priciest = max(pos_ev_ebitda, key=lambda p: p[1]) if pos_ev_ebitda else None

    return {
        "count": len(comps),
        "median_ev_ebitda": _median([v for _, v in ev_ebitda]),
        "median_ev_revenue": _median([v for _, v in ev_rev]),
        "median_rev_growth": _median([v for _, v in revg]),
        "median_rule_of_40": _median([v for _, v in r40]),
        "cheapest_ev_ebitda": cheapest,   # (ticker, value) or None
        "priciest_ev_ebitda": priciest,
    }


# ── Renderer ──────────────────────────────────────────────────────────────────

def render(dataset: SectorDataset, output_path: str) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=False,   # we escape manually in filter functions
    )

    # Register filters
    env.filters["color_class"] = color_class_filter
    env.filters["fmt_num"] = fmt_num
    env.filters["fmt_rev"] = fmt_rev
    env.filters["fmt_b_display"] = fmt_b_display
    env.filters["fmt_ratio_x"] = fmt_ratio_x
    env.filters["fmt_ratio_pct"] = fmt_ratio_pct
    env.filters["fmt_ratio_plain"] = fmt_ratio_plain
    env.filters["dp_tooltip"] = dp_tooltip
    env.filters["ratio_tooltip"] = ratio_tooltip
    env.filters["detail_card"] = detail_card
    env.filters["ratio_detail_card"] = ratio_detail_card

    template = env.get_template("dashboard.html.j2")
    html = template.render(dataset=dataset, summary=build_summary(dataset))

    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
