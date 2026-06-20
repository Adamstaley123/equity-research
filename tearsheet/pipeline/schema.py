from __future__ import annotations
from typing import Literal, Optional
from pydantic import AliasChoices, BaseModel, Field


SourceType = Literal[
    "yfinance",
    "edgar_10k",
    "edgar_10q",
    "stockanalysis_scrape",
    "computed",
    "manual_override",
]


class DataPoint(BaseModel):
    value: Optional[float] = None
    source: Optional[SourceType] = None
    source_detail: Optional[str] = None      # e.g. "edgar_10q:accession=..., concept=Revenues"
    period_label: Optional[str] = None       # e.g. "TTM Q1 2026"
    period_end: Optional[str] = None         # ISO date: "2025-12-31"
    fetched_at: Optional[str] = None         # ISO datetime
    na_reason: Optional[str] = None          # full chain of why every source failed


class ComputedRatio(BaseModel):
    value: Optional[float] = None
    formula: str
    inputs: dict[str, Optional[float]] = Field(default_factory=dict)
    input_labels: dict[str, str] = Field(default_factory=dict)    # "$7.64B", "25.9%", etc.
    na_reason: Optional[str] = None


class CompanyRecord(BaseModel):
    ticker: str
    company_name: str
    # Short description of what the company does within its sector (any sector).
    # Accepts the legacy key "payments_focus" so older saved datasets still load.
    focus: str = Field(default="", validation_alias=AliasChoices("focus", "payments_focus"))
    exchange: str = "NASDAQ"
    currency: str = "USD"
    fx_rate_to_usd: float = 1.0
    fx_rate_source: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    cik: Optional[str] = None               # zero-padded 10-digit SEC CIK
    sector: str
    run_id: str

    # ── Raw fetched data ──────────────────────────────────────────────────────
    market_cap: DataPoint = Field(default_factory=DataPoint)
    enterprise_value: DataPoint = Field(default_factory=DataPoint)
    revenue_ttm: DataPoint = Field(default_factory=DataPoint)
    revenue_prior_year: DataPoint = Field(default_factory=DataPoint)
    gross_profit: DataPoint = Field(default_factory=DataPoint)
    ebitda: DataPoint = Field(default_factory=DataPoint)
    operating_cash_flow: DataPoint = Field(default_factory=DataPoint)
    capex: DataPoint = Field(default_factory=DataPoint)
    net_income_ttm: DataPoint = Field(default_factory=DataPoint)
    total_cash: DataPoint = Field(default_factory=DataPoint)
    total_debt: DataPoint = Field(default_factory=DataPoint)
    eps_ttm: DataPoint = Field(default_factory=DataPoint)
    eps_forward: DataPoint = Field(default_factory=DataPoint)
    eps_growth_estimate: DataPoint = Field(default_factory=DataPoint)
    shares_outstanding: DataPoint = Field(default_factory=DataPoint)
    current_price: DataPoint = Field(default_factory=DataPoint)

    # ── Computed ratios ────────────────────────────────────────────────────────
    fcf: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="OperatingCashFlow - CapEx"))
    ev_revenue: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="EV / Revenue_TTM"))
    ev_ebitda: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="EV / EBITDA"))
    ev_fcf: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="EV / FCF"))
    pe_ttm: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="MarketCap / NetIncome_TTM"))
    pe_forward: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="Price / EPS_Forward"))
    peg_ratio: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="ForwardPE / EPSGrowthPct"))
    price_sales: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="MarketCap / Revenue_TTM"))
    price_fcf: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="MarketCap / FCF"))
    gross_margin_pct: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="GrossProfit / Revenue_TTM"))
    ebitda_margin_pct: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="EBITDA / Revenue_TTM"))
    fcf_margin_pct: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="FCF / Revenue_TTM"))
    revenue_growth_yoy_pct: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="(Revenue_TTM - Revenue_PriorYear) / Revenue_PriorYear"))
    net_debt: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="TotalDebt - TotalCash"))
    net_debt_ebitda: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="NetDebt / EBITDA"))
    rule_of_40: ComputedRatio = Field(default_factory=lambda: ComputedRatio(formula="RevGrowthPct + FCFMarginPct"))


class SectorDataset(BaseModel):
    sector: str
    run_id: str
    generated_at: str
    tickers: list[str]
    companies: dict[str, CompanyRecord] = Field(default_factory=dict)
    pipeline_version: str = "1.0.0"
    methodology_notes: list[str] = Field(default_factory=list)
