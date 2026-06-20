"""
StockAnalysis.com scrape fetcher — OPT-IN fallback when EDGAR and yfinance fail.

⚠ Opt-in only. This module is NOT used unless the pipeline is run with
`--use-scrape`. Scraping StockAnalysis.com may violate their Terms of Service,
so it is disabled by default and the user is responsible for their own use.
SEC EDGAR (public domain) and Yahoo Finance are the default sources.

When enabled, it is used as a last-resort fallback for:
  - Foreign private issuers (e.g. ADYEN) that don't have EDGAR XBRL data
  - Any field where EDGAR returns None

Scraping is inherently fragile; all values fetched here are tagged
source="stockanalysis_scrape".
"""
from __future__ import annotations
import re
import requests
from bs4 import BeautifulSoup

from pipeline.schema import DataPoint
from pipeline.fetchers.base import make_dp, na_dp

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_US_STATS_URL = "https://stockanalysis.com/stocks/{ticker}/statistics/"
_INTL_STATS_URL = "https://stockanalysis.com/quote/ams/{ticker}/statistics/"


def _fetch_page(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _parse_value(text: str) -> tuple[Optional[float], str]:
    """
    Parse a text like '$6.15B', '25.9%', '1.72x', '-$351M' into a float.
    Returns (value_or_None, unit_suffix).
    """
    import re
    text = text.strip().replace(",", "").replace("$", "").replace("%", "").replace("x", "")
    negative = text.startswith("-") or text.startswith("(")
    text = text.lstrip("-(").rstrip(")")
    multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
    suffix = ""
    factor = 1.0
    if text and text[-1].upper() in multipliers:
        suffix = text[-1].upper()
        factor = multipliers[suffix]
        text = text[:-1]
    try:
        val = float(text) * factor
        return (-val if negative else val), suffix
    except (ValueError, TypeError):
        return None, ""


def _find_row_value(soup: BeautifulSoup, label_pattern: str) -> Optional[str]:
    """Find a table row whose label matches the pattern and return the value cell text."""
    pattern = re.compile(label_pattern, re.IGNORECASE)
    for td in soup.find_all("td"):
        if pattern.search(td.get_text(strip=True)):
            sibling = td.find_next_sibling("td")
            if sibling:
                return sibling.get_text(strip=True)
    return None


def _scrape_statistics(ticker: str, is_international: bool = False) -> dict[str, str]:
    """Return a raw dict of {label: value_text} from the statistics page."""
    url = (
        _INTL_STATS_URL.format(ticker=ticker)
        if is_international
        else _US_STATS_URL.format(ticker=ticker)
    )
    soup = _fetch_page(url)
    result: dict[str, str] = {}
    rows = soup.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if label and value:
                result[label] = value
    return result


_PRICE_FIELDS = {"current_price", "market_cap", "enterprise_value"}

def _dp_from_raw(
    raw: dict[str, str],
    label_patterns: list[str],
    field_name: str,
    ticker: str,
    url: str,
    multiplier: float = 1.0,
) -> DataPoint:
    """Try each label pattern against the scraped rows; return DataPoint."""
    is_price_field = field_name in _PRICE_FIELDS
    for pattern in label_patterns:
        for label, value_text in raw.items():
            if re.search(pattern, label, re.IGNORECASE):
                val, _ = _parse_value(value_text)
                if val is not None:
                    # Prices must be positive — negative values are % change rows or artifacts
                    if is_price_field and val < 0:
                        continue
                    val *= multiplier
                    return make_dp(
                        value=val,
                        source="stockanalysis_scrape",
                        source_detail=f"stockanalysis_scrape:url={url},label={label!r}",
                        period_label="TTM (StockAnalysis)",
                    )
    return na_dp(
        f"stockanalysis_scrape: label not found for {field_name} "
        f"(tried {label_patterns}) on {url}"
    )


# ── Public fetch functions ────────────────────────────────────────────────────

def fetch_all(ticker: str, is_international: bool = False) -> dict[str, DataPoint]:
    """
    Scrape the StockAnalysis statistics page for a ticker and return a dict
    of field_name → DataPoint for all fields we can parse.

    Returns an empty dict on scrape failure (caller falls through to N/A).
    """
    url = (
        _INTL_STATS_URL.format(ticker=ticker)
        if is_international
        else _US_STATS_URL.format(ticker=ticker)
    )
    try:
        raw = _scrape_statistics(ticker, is_international=is_international)
    except Exception as exc:
        return {"_error": na_dp(f"StockAnalysis scrape failed for {ticker}: {exc}")}

    def dp(patterns: list[str], field: str, mult: float = 1.0) -> DataPoint:
        return _dp_from_raw(raw, patterns, field, ticker, url, mult)

    return {
        "market_cap":            dp(["Market Cap", "Mkt Cap"], "market_cap"),
        "enterprise_value":      dp(["Enterprise Value", "EV"], "enterprise_value"),
        "revenue_ttm":           dp(["Revenue.*TTM", "Total Revenue", r"^Revenue$"], "revenue_ttm"),
        "gross_profit":          dp(["Gross Profit"], "gross_profit"),
        "ebitda":                dp([r"^EBITDA$", "EBITDA.*TTM"], "ebitda"),
        "operating_cash_flow":   dp(["Operating Cash Flow", "Cash from Operations", r"^Operating Cash"], "operating_cash_flow"),
        "capex":                 dp(["Capital Expenditure", "CapEx", "Capital Expenditures"], "capex"),
        "net_income_ttm":        dp(["Net Income.*TTM", "Net Income"], "net_income_ttm"),
        "total_cash":            dp(["Cash.*Equiv", "Total Cash"], "total_cash"),
        "total_debt":            dp(["Total Debt", "Long.Term Debt"], "total_debt"),
        "current_price":         dp(["Current Price", "Price"], "current_price"),
        "eps_forward":           dp(["EPS.*Forward", "Forward EPS"], "eps_forward"),
        "eps_growth_estimate":   dp(["EPS Growth", "Earnings Growth"], "eps_growth_estimate"),
        "shares_outstanding":    dp(["Shares Outstanding"], "shares_outstanding"),
    }
