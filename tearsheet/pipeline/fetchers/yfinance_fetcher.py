"""
yfinance fetcher — used ONLY for market data and analyst estimates.

Fields we trust from yfinance:
  - currentPrice, marketCap, sharesOutstanding
  - forwardEps, trailingEps, pegRatio (analyst estimates — labeled as such)

Fields we do NOT use from yfinance:
  - enterpriseValue (known stale-value bug)
  - totalRevenue, ebitda, freeCashflow, grossMargins (unreliable, use EDGAR)
"""
from __future__ import annotations
from typing import Optional
from pipeline.schema import DataPoint
from pipeline.fetchers.base import make_dp, na_dp

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


def _get_info(ticker: str, yfinance_ticker: Optional[str] = None) -> dict:
    """
    Fetch yfinance info. Uses yfinance_ticker if provided (e.g. 'FISV' for FI, 'ADYEN.AS' for ADYEN).
    Falls back to the dashboard ticker symbol if yfinance_ticker is not given or fails.
    """
    if not _YF_AVAILABLE:
        raise RuntimeError("yfinance not installed — run: pip install yfinance")
    symbols = []
    if yfinance_ticker:
        symbols.append(yfinance_ticker)
    if ticker not in symbols:
        symbols.append(ticker)

    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            # Confirm we got real data (not just an empty/error shell)
            if info.get("marketCap") or info.get("currentPrice") or info.get("regularMarketPrice"):
                return info
        except Exception:
            continue
    return {}


def fetch_current_price(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    info = _get_info(ticker, yfinance_ticker)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None:
        return na_dp(f"yfinance: currentPrice None for {yfinance_ticker or ticker}")
    return make_dp(
        value=float(price),
        source="yfinance",
        source_detail=f"yfinance:ticker.info.currentPrice ({yfinance_ticker or ticker})",
        period_label="live",
    )


def fetch_market_cap(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    info = _get_info(ticker, yfinance_ticker)
    mc = info.get("marketCap")
    if mc is None:
        return na_dp(f"yfinance: marketCap None for {yfinance_ticker or ticker}")
    return make_dp(
        value=float(mc),
        source="yfinance",
        source_detail=f"yfinance:ticker.info.marketCap ({yfinance_ticker or ticker})",
        period_label="live",
    )


def fetch_shares_outstanding(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    info = _get_info(ticker, yfinance_ticker)
    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if shares is None:
        return na_dp(f"yfinance: sharesOutstanding None for {yfinance_ticker or ticker}")
    return make_dp(
        value=float(shares),
        source="yfinance",
        source_detail=f"yfinance:ticker.info.sharesOutstanding ({yfinance_ticker or ticker})",
        period_label="live",
    )


def fetch_eps_forward(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    info = _get_info(ticker, yfinance_ticker)
    eps = info.get("forwardEps")
    if eps is None:
        return na_dp(f"yfinance: forwardEps None for {yfinance_ticker or ticker} (analyst estimate not available)")
    return make_dp(
        value=float(eps),
        source="yfinance",
        source_detail=f"yfinance:ticker.info.forwardEps ({yfinance_ticker or ticker}) [analyst consensus]",
        period_label="NTM estimate",
    )


def fetch_eps_growth_estimate(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    """EPS growth estimate for PEG ratio — analyst estimate, labeled as such."""
    info = _get_info(ticker, yfinance_ticker)
    growth = info.get("earningsGrowth") or info.get("revenueGrowth")
    if growth is None:
        return na_dp(f"yfinance: earningsGrowth None for {yfinance_ticker or ticker}")
    return make_dp(
        value=float(growth) * 100,   # 0.25 → 25.0
        source="yfinance",
        source_detail=f"yfinance:ticker.info.earningsGrowth ({yfinance_ticker or ticker}) [analyst estimate]",
        period_label="NTM estimate",
    )


def fetch_peg_ratio(ticker: str, yfinance_ticker: Optional[str] = None) -> DataPoint:
    """PEG ratio sourced directly from Yahoo (P/E ÷ 3-5yr expected EPS growth).

    We source rather than self-compute: the proper PEG denominator is a long-term
    forward growth estimate, which yfinance exposes as trailingPegRatio. Dividing
    forward P/E by the volatile *trailing quarterly* earningsGrowth (our old
    approach) produced wildly wrong PEGs (e.g. WEX 0.32 vs Yahoo 0.86).
    """
    info = _get_info(ticker, yfinance_ticker)
    peg = info.get("trailingPegRatio") or info.get("pegRatio")
    if peg is None:
        return na_dp(f"yfinance: trailingPegRatio None for {yfinance_ticker or ticker} (analyst LT growth estimate not available)")
    return make_dp(
        value=float(peg),
        source="yfinance",
        source_detail=f"yfinance:ticker.info.trailingPegRatio ({yfinance_ticker or ticker}) [Yahoo PEG, 3-5yr est. growth]",
        period_label="Yahoo PEG (est.)",
    )


# yfinance exchange codes → friendly display names (best-effort; falls back to
# fullExchangeName or the raw code for anything not listed).
_EXCHANGE_NAMES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NSC": "NASDAQ",
    "NYQ": "NYSE", "PCX": "NYSE Arca", "ASE": "NYSE American",
    "AMS": "AMS", "LSE": "LSE", "GER": "ETR", "EBS": "SWX",
}


def fetch_profile(ticker: str, yfinance_ticker: Optional[str] = None) -> dict:
    """Best-effort company profile from yfinance, used ONLY to auto-fill metadata
    a user didn't supply for an ad-hoc (no-YAML) run. Returns a dict with any of
    {name, focus, exchange, currency} — missing keys are omitted. Never overrides
    a value the sector YAML already provided (the caller fills blanks only).
    """
    try:
        info = _get_info(ticker, yfinance_ticker)
    except Exception:
        return {}
    out: dict = {}
    name = info.get("longName") or info.get("shortName")
    if name:
        out["name"] = name
    focus = info.get("industry") or info.get("sector")
    if focus:
        out["focus"] = focus
    exch = info.get("exchange")
    if exch:
        out["exchange"] = _EXCHANGE_NAMES.get(exch, info.get("fullExchangeName") or exch)
    cur = info.get("currency")
    if cur:
        out["currency"] = cur
    return out


def fetch_fx_rate(from_currency: str, to_currency: str = "USD") -> DataPoint:
    """Fetch FX rate, e.g. EUR→USD via yfinance EURUSD=X ticker."""
    if from_currency == to_currency:
        return make_dp(value=1.0, source="yfinance", source_detail="identity (same currency)", period_label="live")
    if not _YF_AVAILABLE:
        raise RuntimeError("yfinance not installed")
    pair = f"{from_currency}{to_currency}=X"
    t = yf.Ticker(pair)
    info = t.info or {}
    rate = info.get("regularMarketPrice") or info.get("currentPrice")
    if rate is None:
        return na_dp(f"yfinance: FX rate {pair} not available")
    return make_dp(value=float(rate), source="yfinance", source_detail=f"yfinance:{pair}.regularMarketPrice", period_label="live")
