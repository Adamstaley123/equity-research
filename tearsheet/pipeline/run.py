"""
Deterministic valuation pipeline CLI.

Usage:
  python3 pipeline/run.py --sector payments --tickers TOST FI FOUR ...

The pipeline:
  1. For each ticker: fetch market data (yfinance) + financials (EDGAR XBRL)
     with StockAnalysis as fallback
  2. Compute all ratios from raw data (pure functions, no AI)
  3. Save raw JSON + full dataset JSON
  4. Render HTML dashboard with expandable per-metric detail cards
"""
from __future__ import annotations
import json
import os
import re
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import typer

# Ensure project root is on sys.path when run from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.schema import CompanyRecord, ComputedRatio, DataPoint, SectorDataset
from pipeline.config import PIPELINE_VERSION
from pipeline import compute as C
from pipeline.fetchers import edgar_fetcher as EF
from pipeline.fetchers import yfinance_fetcher as YF
from pipeline.fetchers import stockanalysis_fetcher as SA
from pipeline.fetchers.base import try_sources, na_dp, make_dp
from pipeline.sectors import load_sector
from pipeline import render as R

# Per-company fetches run in a small thread pool. Kept low so concurrent EDGAR
# calls (globally rate-limited in edgar_fetcher) stay well under SEC's 10 req/sec
# limit; yfinance/StockAnalysis calls parallelize freely.
FETCH_WORKERS = 4

app = typer.Typer(add_completion=False)


def _run_id(sector: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{sector}_{today}"


def _slug(s: str) -> str:
    """Filesystem/run-id-safe slug from a display name (e.g. 'Consumer Staples'
    → 'consumer_staples'). Used to name ad-hoc sectors from --name/--tickers."""
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")
    return s or "custom"


def _fetch_company(
    ticker: str,
    meta: dict,
    sector: str,
    run_id: str,
    skip_edgar: bool = False,
    skip_scrape: bool = False,
) -> CompanyRecord:
    # ── Auto-enrich missing metadata from yfinance ───────────────────────────
    # For ad-hoc runs (user passed only --tickers, no YAML) the per-company meta
    # is empty. Fill name/focus/exchange/currency from Yahoo so the dashboard is
    # complete. Curated YAML values ALWAYS win — we only fill blanks, and one
    # extra yfinance call happens only when something is actually missing (so the
    # curated payments path makes no extra calls and renders identically).
    _missing = [k for k in ("name", "focus", "exchange", "currency") if not meta.get(k)]
    if _missing:
        try:
            _profile = YF.fetch_profile(ticker, meta.get("yfinance_ticker"))
        except Exception:
            _profile = {}
        if _profile:
            meta = {**meta, **{k: _profile[k] for k in _missing if _profile.get(k)}}

    currency = meta.get("currency", "USD")
    # Foreign (non-EDGAR) issuer: set `foreign: true` in the sector YAML, or tag
    # `european` in flags, or use a non-US exchange. Controls the scrape fallback's
    # URL form. Generalizes the old AMS-only check so any sector can include
    # foreign names, not just payments/Adyen.
    flags_meta = meta.get("flags", [])
    is_intl = bool(
        meta.get("foreign")
        or "european" in flags_meta
        or meta.get("exchange") in {"AMS", "LSE", "ETR", "EPA", "TSE", "HKG", "TYO"}
    )

    # Use hardcoded CIK override if available — avoids false matches for short tickers (FI→eBay, etc.)
    cik_override = meta.get("cik")
    yfinance_ticker = meta.get("yfinance_ticker")  # e.g. "FISV" for FI, "ADYEN.AS" for ADYEN

    if cik_override:
        cik = cik_override
        typer.echo(f"  [{ticker}] Using hardcoded CIK = {cik}")
    elif not skip_edgar:
        typer.echo(f"  [{ticker}] Fetching CIK...")
        cik = EF.get_cik(ticker)
        if cik:
            typer.echo(f"  [{ticker}] CIK = {cik}")
        else:
            typer.echo(f"  [{ticker}] CIK not found — EDGAR disabled for this ticker")
    else:
        cik = None

    # ── FX rate ──────────────────────────────────────────────────────────────
    fx_rate = 1.0
    fx_source = "identity"
    if currency != "USD":
        try:
            fx_dp = YF.fetch_fx_rate(currency, "USD")
            if fx_dp.value:
                fx_rate = fx_dp.value
                fx_source = fx_dp.source_detail or "yfinance"
        except Exception:
            pass

    # ── StockAnalysis scrape (lazy — fetched once, used as fallback) ──────────
    sa_data: dict[str, DataPoint] = {}
    if not skip_scrape:
        typer.echo(f"  [{ticker}] Scraping StockAnalysis (fallback data)...")
        try:
            sa_data = SA.fetch_all(ticker, is_international=is_intl)
        except Exception as exc:
            typer.echo(f"  [{ticker}] StockAnalysis scrape failed: {exc}", err=True)

    def sa(field: str) -> DataPoint:
        fallback = na_dp(
            f"StockAnalysis fallback disabled (run with --use-scrape to enable)"
            if skip_scrape else
            f"StockAnalysis: field {field!r} not available"
        )
        return sa_data.get(field, fallback)

    # ── Fetch + assemble (split into focused helpers) ─────────────────────────
    market = _fetch_market(ticker, yfinance_ticker, cik, skip_edgar, sa)
    fin = _fetch_financials(ticker, cik, skip_edgar, sa)
    return _build_record(
        ticker, meta, sector, run_id, cik, currency, fx_rate, fx_source, market, fin, sa,
    )


def _fetch_market(ticker: str, yft: Optional[str], cik: Optional[str],
                  skip_edgar: bool, sa) -> dict:
    """yfinance-primary market data: price, basic shares, market cap (computed
    price×shares), forward EPS, EPS-growth estimate, and Yahoo PEG. StockAnalysis
    is the fallback for each (via `sa`)."""
    typer.echo(f"  [{ticker}] Fetching market data (yfinance)...")

    current_price = try_sources("current_price", [
        ("yfinance", lambda: YF.fetch_current_price(ticker, yft)),
        ("stockanalysis", lambda: sa("current_price")),
    ])
    shares = try_sources("shares_outstanding", [
        ("yfinance", lambda: YF.fetch_shares_outstanding(ticker, yft)),
        ("edgar" if cik else "skip", lambda: EF.fetch_shares_outstanding(cik) if cik else na_dp("no CIK")),
        ("stockanalysis", lambda: sa("shares_outstanding")),
    ])
    # Compute market cap from price × basic shares outstanding rather than using yfinance's
    # pre-computed marketCap field, which uses impliedSharesOutstanding (includes unconverted
    # founder LLC units, options, etc.) and can be stale after corporate restructurings.
    # Example: FOUR's Up-C Collapse (Feb 2026) reduced share count; yfinance still returned
    # the old implied count for weeks after.
    if current_price.value is not None and shares.value is not None:
        _mc_val = current_price.value * shares.value
        market_cap = make_dp(
            value=_mc_val,
            source="computed",
            source_detail=(
                f"computed:price({current_price.source_detail}) × "
                f"sharesOutstanding({shares.source_detail}) = "
                f"{current_price.value:.2f} × {shares.value/1e6:.2f}M = {_mc_val/1e9:.3f}B"
            ),
            period_label="live",
        )
    else:
        market_cap = try_sources("market_cap", [
            ("yfinance", lambda: YF.fetch_market_cap(ticker, yft)),
            ("stockanalysis", lambda: sa("market_cap")),
        ])
    eps_fwd = try_sources("eps_forward", [
        ("yfinance", lambda: YF.fetch_eps_forward(ticker, yft)),
        ("stockanalysis", lambda: sa("eps_forward")),
    ])
    eps_growth = try_sources("eps_growth_estimate", [
        ("yfinance", lambda: YF.fetch_eps_growth_estimate(ticker, yft)),
        ("stockanalysis", lambda: sa("eps_growth_estimate")),
    ])
    # PEG sourced from Yahoo (P/E ÷ 3-5yr est. growth) rather than self-computed
    # from volatile trailing-quarter growth.
    peg_sourced = try_sources("peg_ratio", [
        ("yfinance", lambda: YF.fetch_peg_ratio(ticker, yft)),
        ("stockanalysis", lambda: sa("peg_ratio")),
    ])
    return {
        "current_price": current_price, "shares": shares, "market_cap": market_cap,
        "eps_fwd": eps_fwd, "eps_growth": eps_growth, "peg_sourced": peg_sourced,
    }


def _fetch_financials(ticker: str, cik: Optional[str], skip_edgar: bool, sa) -> dict:
    """EDGAR-primary financial statement items (TTM), plus the matched annual
    revenue pair for YoY growth. Returned RAW — pre-FX and pre-gross-profit
    resolution, which `_build_record` applies."""
    typer.echo(f"  [{ticker}] Fetching financials (EDGAR)...")

    def edgar_or_sa(edgar_fn, sa_field: str, label: str) -> DataPoint:
        sources = []
        if cik and not skip_edgar:
            sources.append(("edgar", edgar_fn))
        sources.append(("stockanalysis", lambda: sa(sa_field)))
        return try_sources(label, sources)

    # Revenue for YoY growth: most-recent FY vs prior FY (matched annual pair, so
    # both endpoints are the same fiscal length/month — no TTM-vs-annual mismatch).
    if cik and not skip_edgar:
        revenue_cur_fy, revenue_prior = EF.fetch_revenue_annual_pair(cik)
    else:
        revenue_cur_fy = na_dp("No EDGAR CIK; current-FY revenue unavailable for growth")
        revenue_prior = na_dp("No EDGAR CIK; prior-year revenue unavailable for growth calculation")

    return {
        "revenue_ttm": edgar_or_sa(lambda: EF.fetch_revenue_ttm(cik), "revenue_ttm", "revenue_ttm"),
        "gp_edgar": EF.fetch_gross_profit(cik) if (cik and not skip_edgar) else na_dp("no CIK"),
        "cost_of_rev": EF.fetch_cost_of_revenue(cik) if (cik and not skip_edgar) else na_dp("no CIK"),
        "op_income": edgar_or_sa(lambda: EF.fetch_operating_income(cik), "ebitda", "operating_income"),
        "da": edgar_or_sa(lambda: EF.fetch_da(cik), "ebitda", "depreciation_amortization"),
        "net_income": edgar_or_sa(lambda: EF.fetch_net_income(cik), "net_income_ttm", "net_income_ttm"),
        "ocf": edgar_or_sa(lambda: EF.fetch_operating_cash_flow(cik), "operating_cash_flow", "operating_cash_flow"),
        "capex": edgar_or_sa(lambda: EF.fetch_capex(cik), "capex", "capex"),
        "total_cash": edgar_or_sa(lambda: EF.fetch_total_cash(cik), "total_cash", "total_cash"),
        "total_debt": edgar_or_sa(lambda: EF.fetch_total_debt(cik), "total_debt", "total_debt"),
        "revenue_cur_fy": revenue_cur_fy,
        "revenue_prior": revenue_prior,
    }


def _build_record(ticker, meta, sector, run_id, cik, currency, fx_rate, fx_source,
                  market: dict, fin: dict, sa) -> CompanyRecord:
    """Resolve gross profit, apply FX, assemble EBITDA, compute every ratio + flag,
    and build the CompanyRecord. Pure assembly over already-fetched DataPoints."""
    current_price = market["current_price"]; shares = market["shares"]
    market_cap = market["market_cap"]; eps_fwd = market["eps_fwd"]
    eps_growth = market["eps_growth"]; peg_sourced = market["peg_sourced"]
    revenue_ttm = fin["revenue_ttm"]; gp_edgar = fin["gp_edgar"]; cost_of_rev = fin["cost_of_rev"]
    op_income = fin["op_income"]; da = fin["da"]; net_income = fin["net_income"]
    ocf = fin["ocf"]; capex = fin["capex"]; total_cash = fin["total_cash"]; total_debt = fin["total_debt"]
    revenue_cur_fy = fin["revenue_cur_fy"]; revenue_prior = fin["revenue_prior"]

    # ── Gross profit — keep it on the SAME basis as revenue ──────────────────────
    # Mixing a scraped gross profit with EDGAR revenue produced >100% margins
    # (WEX, PAYO, GDOT) because the two providers use different revenue definitions.
    # Priority: EDGAR GrossProfit → EDGAR Revenue−Cost → StockAnalysis (only if
    # consistent: 0 ≤ GP ≤ revenue). Done pre-FX so fx_scale applies.
    rev = revenue_ttm.value
    if gp_edgar.value is not None:
        gross_profit = gp_edgar
    elif rev is not None and cost_of_rev.value is not None:
        gp_val = rev - cost_of_rev.value
        gross_profit = make_dp(
            value=gp_val, source="computed",
            source_detail=(
                f"computed:Revenue({rev/1e6:.0f}M)−Cost({cost_of_rev.value/1e6:.0f}M)"
                f"={gp_val/1e6:.0f}M [GrossProfit not tagged]"
            ),
            period_label=revenue_ttm.period_label, period_end=revenue_ttm.period_end,
        )
    else:
        gp_sa = sa("gross_profit")
        if gp_sa.value is not None and rev is not None and 0 <= gp_sa.value <= rev:
            gross_profit = gp_sa
        elif gp_sa.value is not None and rev is not None and gp_sa.value > rev:
            gross_profit = na_dp(
                f"gross profit unavailable from EDGAR; StockAnalysis GP "
                f"({gp_sa.value/1e9:.2f}B) exceeds EDGAR revenue ({rev/1e9:.2f}B) — "
                f"inconsistent revenue bases, rejected to avoid >100% margin"
            )
        else:
            gross_profit = na_dp("gross profit unavailable from EDGAR or a consistent source")

    # Scale by FX if non-USD
    def fx_scale(dp: DataPoint) -> DataPoint:
        if fx_rate != 1.0 and dp.value is not None:
            return dp.model_copy(update={"value": dp.value * fx_rate,
                                         "source_detail": (dp.source_detail or "") + f" [×{fx_rate:.4f} {currency}/USD]"})
        return dp

    revenue_ttm  = fx_scale(revenue_ttm)
    gross_profit = fx_scale(gross_profit)
    op_income    = fx_scale(op_income)
    da           = fx_scale(da)
    net_income   = fx_scale(net_income)
    ocf          = fx_scale(ocf)
    capex        = fx_scale(capex)
    total_cash   = fx_scale(total_cash)
    total_debt   = fx_scale(total_debt)
    market_cap   = fx_scale(market_cap)

    # ── EBITDA = Operating Income + D&A ──────────────────────────────────────
    # D&A is often not tagged in EDGAR for all companies; fall back to op_income when missing.
    # Guard: if D&A >= 60% of op_income, the D&A data is likely unreliable (e.g. foreign ADR filers
    # where the XBRL concept returns a wrong value). Use op_income-only proxy in that case.
    da_reliable = (
        da.value is not None
        and op_income.value is not None
        and (op_income.value == 0 or abs(da.value) < abs(op_income.value) * 0.6)
    )
    if op_income.value is not None and da_reliable:
        ebitda_val = op_income.value + da.value
        ebitda_raw = op_income.model_copy(update={
            "value": ebitda_val,
            "source_detail": (
                f"computed:OpIncome({op_income.value/1e6:.0f}M)"
                f"+D&A({da.value/1e6:.0f}M)"
                f"={ebitda_val/1e6:.0f}M"
            ),
        })
    elif op_income.value is not None:
        # D&A unavailable — use operating income as proxy and flag it
        ebitda_raw = op_income.model_copy(update={
            "source_detail": (op_income.source_detail or "") + " [D&A unavailable; EBITDA≈OpIncome]",
        })
    else:
        ebitda_raw = op_income  # both None

    # ── Compute ratios ────────────────────────────────────────────────────────
    fcf = C.compute_fcf(ocf, capex)

    # EV = market_cap + total_debt - total_cash (computed from components)
    ev_val: Optional[float] = None
    ev_detail = "Computed: MarketCap + TotalDebt − TotalCash"
    if market_cap.value is not None:
        debt = total_debt.value or 0.0
        cash = total_cash.value or 0.0
        ev_val = market_cap.value + debt - cash
        ev_detail += f" = {market_cap.value/1e9:.2f}B + {debt/1e9:.2f}B − {cash/1e9:.2f}B"
    enterprise_value = DataPoint(
        value=ev_val,
        source="computed",
        source_detail=ev_detail,
        period_label="live",
        fetched_at=market_cap.fetched_at,
        na_reason=None if ev_val is not None else "MarketCap unavailable — cannot compute EV",
    )

    ev_revenue       = C.compute_ev_revenue(enterprise_value, revenue_ttm)
    ev_ebitda        = C.compute_ev_ebitda(enterprise_value, ebitda_raw)
    ev_fcf           = C.compute_ev_fcf(enterprise_value, fcf)
    pe_ttm           = C.compute_pe_ttm(market_cap, net_income)
    pe_forward       = C.compute_pe_forward(current_price, eps_fwd)
    # PEG: wrap the Yahoo-sourced value as a ComputedRatio so render/template are unchanged
    peg_ratio        = ComputedRatio(
        value=peg_sourced.value,
        formula="Yahoo PEG = P/E ÷ 3-5yr est. EPS growth (sourced)",
        inputs={"YahooPEG": peg_sourced.value},
        input_labels={"YahooPEG": f"{peg_sourced.value:.2f}" if peg_sourced.value is not None else "N/A"},
        na_reason=None if peg_sourced.value is not None else (peg_sourced.na_reason or "PEG unavailable"),
    )
    price_sales      = C.compute_price_sales(market_cap, revenue_ttm)
    price_fcf        = C.compute_price_fcf(market_cap, fcf)
    gross_margin     = C.compute_gross_margin(gross_profit, revenue_ttm)
    ebitda_margin    = C.compute_ebitda_margin(ebitda_raw, revenue_ttm)
    fcf_margin       = C.compute_fcf_margin(fcf, revenue_ttm)
    rev_growth       = C.compute_revenue_growth_yoy(revenue_cur_fy, revenue_prior)
    net_debt         = C.compute_net_debt(total_debt, total_cash)
    net_debt_ebitda  = C.compute_net_debt_ebitda(net_debt, ebitda_raw)
    rule_of_40       = C.compute_rule_of_40(rev_growth, fcf_margin)

    # ── Flags ─────────────────────────────────────────────────────────────────
    flags = list(meta.get("flags", []))
    if enterprise_value.value is not None and enterprise_value.value < 0:
        flags.append("negative_ev")
    # SBC-heavy: FCF materially exceeds EBITDA — only meaningful when EBITDA > 0
    if (fcf.value is not None
            and ebitda_raw.value is not None
            and ebitda_raw.value > 0
            and fcf.value > ebitda_raw.value * 1.5):
        if "sbc_heavy" not in flags:
            flags.append("sbc_heavy")

    return CompanyRecord(
        ticker=ticker,
        company_name=meta.get("name", ticker),
        focus=meta.get("focus", ""),
        exchange=meta.get("exchange", "NASDAQ"),
        currency=currency,
        fx_rate_to_usd=fx_rate,
        fx_rate_source=fx_source,
        flags=flags,
        cik=cik,
        sector=sector,
        run_id=run_id,
        market_cap=market_cap,
        enterprise_value=enterprise_value,
        revenue_ttm=revenue_ttm,
        revenue_prior_year=revenue_prior,
        gross_profit=gross_profit,
        ebitda=ebitda_raw,
        operating_cash_flow=ocf,
        capex=capex,
        net_income_ttm=net_income,
        total_cash=total_cash,
        total_debt=total_debt,
        eps_forward=eps_fwd,
        eps_growth_estimate=eps_growth,
        shares_outstanding=shares,
        current_price=current_price,
        fcf=fcf,
        ev_revenue=ev_revenue,
        ev_ebitda=ev_ebitda,
        ev_fcf=ev_fcf,
        pe_ttm=pe_ttm,
        pe_forward=pe_forward,
        peg_ratio=peg_ratio,
        price_sales=price_sales,
        price_fcf=price_fcf,
        gross_margin_pct=gross_margin,
        ebitda_margin_pct=ebitda_margin,
        fcf_margin_pct=fcf_margin,
        revenue_growth_yoy_pct=rev_growth,
        net_debt=net_debt,
        net_debt_ebitda=net_debt_ebitda,
        rule_of_40=rule_of_40,
    )


@app.command()
def main(
    sector: Optional[str] = typer.Option(
        None, help="Sector name (e.g. 'payments') — loads sectors/<name>.yaml. "
                   "Optional: omit it and pass --tickers for an ad-hoc run with no YAML."),
    name: Optional[str] = typer.Option(
        None, "--name", help="Display title for an ad-hoc run (used with --tickers when "
                             "there's no YAML), e.g. --name \"Utilities\"."),
    tickers: Optional[list[str]] = typer.Option(
        None, help="Tickers to run — comma-separated (NEE,DUK,SO) or repeated "
                   "(--tickers NEE --tickers DUK). With a curated --sector, omit to run "
                   "the whole sector; without a YAML, this list IS the universe."),
    output_dir: str = typer.Option("data", help="Base output directory"),
    from_json: Optional[str] = typer.Option(None, help="Re-render HTML from existing JSON, skip fetching"),
    no_html: bool = typer.Option(False, help="Output JSON only, skip HTML generation"),
    skip_edgar: bool = typer.Option(False, help="Disable SEC EDGAR fetching"),
    use_scrape: bool = typer.Option(
        False,
        help="Opt in to the StockAnalysis.com scrape fallback (off by default; "
             "scraping may violate their ToS — you are responsible for your use).",
    ),
    open_browser: bool = typer.Option(False, help="Open HTML in browser when done"),
):
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Normalize --tickers: accept comma-separated AND/OR repeated, uppercased.
    if tickers:
        tickers = [t.strip().upper() for item in tickers for t in item.split(",") if t.strip()]

    # ── Resolve the company universe ──────────────────────────────────────────
    # Three paths:
    #   1. Curated YAML   — `--sector payments` (sectors/payments.yaml exists)
    #   2. Ad-hoc tickers — `--tickers NEE,DUK,SO --name Utilities` (no YAML)
    #   3. --from-json     — re-render only, needs --sector for the output path
    sector_meta: dict = {}
    if from_json:
        if not sector:
            typer.echo("Error: --sector is required with --from-json.", err=True)
            raise typer.Exit(code=1)
    else:
        cfg = None
        if sector:
            try:
                cfg = load_sector(sector)
            except FileNotFoundError as exc:
                # A named sector with no YAML is only an error if we also have no
                # tickers to fall back on; otherwise treat `sector` as the title.
                if not tickers:
                    typer.echo(f"Error: {exc}", err=True)
                    raise typer.Exit(code=1)
                cfg = None

        if cfg is not None:
            # ── Path 1: curated sector from YAML ──
            sector_meta = cfg["companies"]
            if not tickers:
                tickers = list(sector_meta.keys())
                if not tickers:
                    typer.echo(f"Error: sector '{sector}' has no companies defined.", err=True)
                    raise typer.Exit(code=1)
        else:
            # ── Path 2: ad-hoc universe straight from --tickers (no YAML) ──
            if not tickers:
                typer.echo(
                    "Error: pass --sector <name> (loads sectors/<name>.yaml) OR "
                    "--tickers T1,T2,... [--name \"Title\"] for an ad-hoc run.",
                    err=True,
                )
                raise typer.Exit(code=1)
            sector = _slug(name or sector or "custom")
            sector_meta = {t: {} for t in tickers}
            typer.echo(
                f"Ad-hoc run ({len(tickers)} ticker(s), no YAML) — company metadata "
                f"will be auto-filled from Yahoo Finance."
            )

    run_id = _run_id(sector)

    # ── Re-render from existing JSON ──────────────────────────────────────────
    if from_json:
        typer.echo(f"Loading dataset from {from_json}...")
        with open(from_json, "r") as f:
            dataset = SectorDataset.model_validate_json(f.read())
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", sector.title())
        html_path = os.path.join(out_dir, f"{sector}_valuation.html")
        typer.echo(f"Rendering HTML → {html_path}")
        R.render(dataset, html_path)
        typer.echo(f"Done. {html_path}")
        if open_browser:
            subprocess.run(["open", html_path], check=False)
        return

    # ── Full pipeline ─────────────────────────────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f"  Valuation Pipeline — {sector.title()} — {run_id}")
    typer.echo(f"  Tickers: {', '.join(tickers)}")
    typer.echo(f"  Sources: SEC EDGAR + Yahoo Finance"
               f"{' + StockAnalysis scrape (opt-in)' if use_scrape else ' (StockAnalysis scrape OFF — pass --use-scrape to enable)'}")
    typer.echo(f"{'='*60}\n")

    companies: dict[str, CompanyRecord] = {}
    failed: list[str] = []

    # Fetch companies concurrently. EDGAR is globally rate-limited inside the
    # fetcher (see edgar_fetcher._throttle), so concurrency stays SEC-safe while
    # yfinance/StockAnalysis I/O overlaps. Each ticker is independent and writes
    # its own raw JSON, so the only shared state is the dicts we update on the
    # main thread as results arrive. The dataset is assembled in `tickers` order
    # afterwards, so output is independent of completion order.
    run_data_dir = os.path.join(output_dir, run_id)
    os.makedirs(run_data_dir, exist_ok=True)

    def _process(ticker: str) -> CompanyRecord:
        meta = sector_meta.get(ticker, {})  # {} → ad-hoc ticker, CIK auto-looked-up
        record = _fetch_company(ticker, meta, sector, run_id, skip_edgar=skip_edgar, skip_scrape=not use_scrape)
        raw_path = os.path.join(run_data_dir, f"raw_{ticker}.json")
        with open(raw_path, "w") as f:
            f.write(record.model_dump_json(indent=2))
        return record

    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(tickers))) as pool:
        futures = {pool.submit(_process, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                companies[ticker] = fut.result()
                typer.echo(f"  [{ticker}] ✓ done")
            except Exception as exc:
                typer.echo(f"  [{ticker}] FAILED: {exc}", err=True)
                failed.append(ticker)

    # ── Assemble dataset ──────────────────────────────────────────────────────
    methodology_notes: list[str] = []
    for t, rec in companies.items():
        na_fields = [
            name for name in ["revenue_ttm", "ebitda", "ev_revenue", "ev_ebitda",
                               "ev_fcf", "pe_ttm", "pe_forward", "fcf"]
            if getattr(rec, name).value is None
        ]
        if na_fields:
            methodology_notes.append(f"{t}: N/A fields — {', '.join(na_fields)}")
        if "negative_ev" in rec.flags:
            methodology_notes.append(f"{t}: Negative EV — EV multiples not meaningful")
        if "sbc_heavy" in rec.flags:
            methodology_notes.append(f"{t}: SBC-heavy — FCF > EBITDA due to stock comp add-back")

    dataset = SectorDataset(
        sector=sector,
        run_id=run_id,
        generated_at=generated_at,
        tickers=[t for t in tickers if t in companies],
        companies=companies,
        pipeline_version=PIPELINE_VERSION,
        methodology_notes=methodology_notes,
    )

    # Save full dataset JSON (date-stamped, gitignored working copy)
    run_data_dir = os.path.join(output_dir, run_id)
    os.makedirs(run_data_dir, exist_ok=True)
    json_path = os.path.join(run_data_dir, f"{sector}_valuation.json")
    dataset_json = dataset.model_dump_json(indent=2)
    with open(json_path, "w") as f:
        f.write(dataset_json)
    typer.echo(f"\n📄 Dataset JSON → {json_path}")

    # Also write a stable, committed copy under examples/data/ — this is the
    # deterministic input the combined multi-sector dashboard (build_combined.py)
    # reads. Newest run wins. Kept next to the curated HTML examples so a clone
    # can rebuild docs/index.html from committed data with no network.
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stable_dir = os.path.join(proj_root, "examples", "data")
    os.makedirs(stable_dir, exist_ok=True)
    stable_path = os.path.join(stable_dir, f"{sector}_dataset.json")
    with open(stable_path, "w") as f:
        f.write(dataset_json)
    typer.echo(f"📌 Stable dataset → {stable_path}")

    # ── Render HTML ───────────────────────────────────────────────────────────
    if not no_html:
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sector_dir = os.path.join(proj_root, "output", sector.title())
        os.makedirs(sector_dir, exist_ok=True)
        html_path = os.path.join(sector_dir, f"{sector}_valuation.html")
        typer.echo(f"🖥  Rendering HTML → {html_path}")
        R.render(dataset, html_path)
        typer.echo(f"✅ Done: {html_path}")
        if open_browser:
            subprocess.run(["open", html_path], check=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f"  Processed: {len(companies)}/{len(tickers)} tickers")
    if failed:
        typer.echo(f"  Failed:    {', '.join(failed)}", err=True)
    if methodology_notes:
        typer.echo(f"\n  Pipeline notes:")
        for note in methodology_notes:
            typer.echo(f"    • {note}")
    typer.echo(f"{'='*60}\n")


if __name__ == "__main__":
    app()
