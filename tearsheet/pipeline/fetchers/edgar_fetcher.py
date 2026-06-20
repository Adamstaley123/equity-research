"""
SEC EDGAR XBRL API fetcher — primary source for all financial statement data.

Endpoints used:
  CIK lookup:     https://efts.sec.gov/LATEST/search-index?q="TICKER"&forms=10-K
  XBRL concept:   https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json
  Submissions:    https://data.sec.gov/submissions/CIK{cik}.json

Rate limit: 10 req/sec — we sleep EDGAR_RATE_LIMIT_SLEEP between calls.
User-Agent header is REQUIRED by SEC or requests are blocked.
"""
from __future__ import annotations
import time
import json
import re
import threading
from datetime import date
from typing import Optional
import requests

from pipeline.config import (
    EDGAR_USER_AGENT, EDGAR_RATE_LIMIT_SLEEP, EDGAR_BASE_URL,
    REVENUE_CONCEPTS, GROSS_PROFIT_CONCEPTS, COST_OF_REVENUE_CONCEPTS,
    OPERATING_INCOME_CONCEPTS,
    NET_INCOME_CONCEPTS, OCF_CONCEPTS, CAPEX_CONCEPTS,
    CASH_CONCEPTS, DEBT_CONCEPTS, SHARES_CONCEPTS, DA_CONCEPTS,
    STALENESS_FLOW_MONTHS, STALENESS_BALANCE_MONTHS,
)
from pipeline.schema import DataPoint
from pipeline.fetchers.base import make_dp, na_dp

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": EDGAR_USER_AGENT, "Accept": "application/json"})

# Global rate limiter. The pipeline fetches companies concurrently (a small
# thread pool), so a bare per-call sleep is NOT enough — N workers would issue
# N× the request rate and could breach SEC's 10 req/sec limit. This lock spaces
# request *initiations* by EDGAR_RATE_LIMIT_SLEEP across ALL threads, keeping the
# global rate under the cap no matter how many workers call in.
_RATE_LOCK = threading.Lock()
_last_request_t = 0.0   # time.monotonic() of the last EDGAR request


def _throttle() -> None:
    global _last_request_t
    with _RATE_LOCK:
        wait = EDGAR_RATE_LIMIT_SLEEP - (time.monotonic() - _last_request_t)
        if wait > 0:
            time.sleep(wait)
        _last_request_t = time.monotonic()


def _get(url: str) -> dict:
    _throttle()
    r = _SESSION.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def lookup_cik(ticker: str) -> Optional[str]:
    """Return zero-padded 10-digit CIK for a ticker, or None if not found."""
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K&dateRange=custom&startdt=2020-01-01"
    try:
        data = _get(url)
        hits = data.get("hits", {}).get("hits", [])
        for hit in hits:
            src = hit.get("_source", {})
            entity_ticker = src.get("period_of_report", "")  # not ticker field
            # Try entity_id or file_date path for CIK
            file_path = hit.get("_id", "")
            cik_match = re.search(r"CIK(\d+)", file_path, re.IGNORECASE)
            if cik_match:
                return cik_match.group(1).zfill(10)
        # Fallback: company search
        url2 = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K"
        data2 = _get(url2)
        hits2 = data2.get("hits", {}).get("hits", [])
        for hit in hits2:
            file_path = hit.get("_id", "")
            cik_match = re.search(r"CIK(\d+)", file_path, re.IGNORECASE)
            if cik_match:
                return cik_match.group(1).zfill(10)
    except Exception:
        pass
    return None


def lookup_cik_from_submissions(ticker: str) -> Optional[str]:
    """Search EDGAR company search page for CIK by ticker symbol."""
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K%2C10-Q"
        data = _get(url)
        hits = data.get("hits", {}).get("hits", [])
        for hit in hits:
            src = hit.get("_source", {})
            entity_name = src.get("display_names", "")
            file_path = hit.get("_id", "")
            cik_match = re.search(r"(\d{10})", file_path)
            if cik_match:
                return cik_match.group(1)
        # Try the company-concept ticker search
        ticker_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker.upper()}%22&forms=10-K"
        data2 = _get(ticker_url)
        hits2 = data2.get("hits", {}).get("hits", [])
        for hit in hits2:
            file_path = hit.get("_id", "")
            cik_match = re.search(r"(\d{10})", file_path)
            if cik_match:
                return cik_match.group(1)
    except Exception:
        pass
    return None


def lookup_cik_direct(ticker: str) -> Optional[str]:
    """Use SEC EDGAR's company tickers JSON (most reliable method)."""
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        data = _get(url)
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def get_cik(ticker: str) -> Optional[str]:
    """Try all CIK lookup methods in order."""
    cik = lookup_cik_direct(ticker)
    if cik:
        return cik
    cik = lookup_cik(ticker)
    if cik:
        return cik
    return lookup_cik_from_submissions(ticker)


def _fetch_concept(cik: str, concept: str) -> Optional[dict]:
    """Fetch a single GAAP concept for a company. Returns raw JSON or None."""
    cik_str = cik if cik.startswith("CIK") else f"CIK{cik}"
    url = f"{EDGAR_BASE_URL}/api/xbrl/companyconcept/{cik_str}/us-gaap/{concept}.json"
    try:
        return _get(url)
    except Exception:
        return None


# ── Staleness anchor ──────────────────────────────────────────────────────────
# The "anchor" is the company's most recent filing period (max reportDate across
# its recent 10-Q/10-K filings). Every fetched value is checked against it so we
# never silently return data from a concept the company stopped tagging years ago.
_ANCHOR_CACHE: dict[str, Optional[str]] = {}


def _get_anchor_period(cik: str) -> Optional[str]:
    """Return the company's latest 10-Q/10-K report date (ISO string), or None."""
    if cik in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[cik]
    cik_str = cik if cik.startswith("CIK") else f"CIK{cik}"
    anchor: Optional[str] = None
    try:
        data = _get(f"{EDGAR_BASE_URL}/submissions/{cik_str}.json")
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        report_dates = recent.get("reportDate", [])
        cands = [
            rd for f, rd in zip(forms, report_dates)
            if f in ("10-Q", "10-K") and rd
        ]
        if cands:
            anchor = max(cands)
    except Exception:
        anchor = None
    _ANCHOR_CACHE[cik] = anchor
    return anchor


def _months_behind(period_end: str, anchor: str) -> Optional[int]:
    """How many whole months `period_end` is before `anchor` (None on parse error)."""
    try:
        pe = date.fromisoformat(period_end)
        an = date.fromisoformat(anchor)
        return (an.year - pe.year) * 12 + (an.month - pe.month)
    except Exception:
        return None


def _staleness_reason(period_end: str, anchor: Optional[str], max_months: int) -> Optional[str]:
    """Return a rejection reason if the value is too old vs the anchor, else None."""
    if not anchor or not period_end:
        return None
    months = _months_behind(period_end, anchor)
    if months is not None and months > max_months:
        return (
            f"value period {period_end} is {months}mo behind latest filing "
            f"{anchor} (max {max_months}mo) — concept likely no longer tagged; "
            f"skipped to avoid stale data"
        )
    return None


def _period_days(entry: dict) -> int:
    """Return number of days the entry covers, or -1 on error."""
    try:
        from datetime import date
        s = date.fromisoformat(entry.get("start", ""))
        e = date.fromisoformat(entry.get("end", ""))
        return (e - s).days
    except Exception:
        return -1


def _is_single_quarter(entry: dict) -> bool:
    """True if entry covers roughly one quarter (70–105 days)."""
    d = _period_days(entry)
    return 70 <= d <= 105


def _extract_ttm_quarters(
    concept_data: dict,
    cik: str,
    concept: str,
) -> tuple[Optional[float], str, str, str]:
    """
    TTM assembly for income-statement items.
    Path 1: sum 4 contiguous single quarters (from 10-Q OR 10-K-carried entries),
            validated so a skipped/unequal quarter can't silently drop a period
            (e.g. PepsiCo's 16-week Q4 — see Path 2).
    Path 2: YTD reconstruction  TTM = FY_prior + YTD_current − YTD_prior, which is
            calendar-correct even when fiscal quarters are unequal length.
    Path 3: most recent FULL-YEAR (~365-day) annual 10-K — never a stray quarter.
    """
    from datetime import date as _date

    units = concept_data.get("units", {})
    usd_entries = units.get("USD", units.get("shares", []))
    if not usd_entries:
        return None, "", "", f"No USD units found for concept {concept}"

    valid = [e for e in usd_entries if e.get("form") in ("10-Q", "10-K") and e.get("val") is not None]
    if not valid:
        return None, "", "", "No 10-Q or 10-K entries found"

    valid.sort(key=lambda e: e.get("end", ""), reverse=True)

    # ── Path 1: 4 contiguous single quarters (any form) ──────────────────────
    # Some filers (e.g. General Mills) tag quarterly gross profit only inside the
    # 10-K, so we accept single-quarter durations regardless of form, then verify
    # the 4 picked quarters actually tile ~one year with no gap/overlap.
    q_single = [e for e in valid if e.get("start") and e.get("end") and _is_single_quarter(e)]
    # Deterministic order: end date descending, and for the same end prefer the
    # as-reported 10-Q over a 10-K-carried (often restated) value.
    q_single.sort(key=lambda e: 0 if e.get("form") == "10-Q" else 1)
    q_single.sort(key=lambda e: e["end"], reverse=True)
    seen_ends: set = set()
    selected = []
    for e in q_single:
        if e["end"] in seen_ends:
            continue
        seen_ends.add(e["end"])
        selected.append(e)
        if len(selected) == 4:
            break
    if len(selected) == 4:
        try:
            newest_end = _date.fromisoformat(selected[0]["end"])
            oldest_start = _date.fromisoformat(selected[3]["start"])
            span = (newest_end - oldest_start).days
            coverage = sum(max(_period_days(e), 0) for e in selected)
            # Genuine trailing-4-quarters: the 4 pieces tile ~one year (span ≈
            # coverage ≈ 365d). Rejects sets that skip a long quarter (coverage ≪
            # span, e.g. PepsiCo) or repeat a quarter across years (span ≫ 1y).
            if 340 <= span <= 380 and abs(span - coverage) <= 20:
                ttm = sum(e["val"] for e in selected)
                accessions = [e.get("accn", "?") for e in selected]
                return ttm, f"TTM ({selected[0]['end']} latest quarter)", selected[0]["end"], f"edgar_10q:accessions={accessions[:2]}...,concept={concept}"
        except Exception:
            pass

    # ── Path 2: YTD reconstruction (handles unequal quarters) ────────────────
    quarterly = [e for e in valid if e.get("form") == "10-Q" and e.get("start") and e.get("end")]
    if quarterly:
        latest_end = quarterly[0]["end"]
        # Income-statement 10-Qs carry both a 3-month AND a YTD-cumulative value
        # for the same end date; pick the longest (the YTD) for the reconstruction.
        ytd_cur = max(
            (e for e in quarterly if e["end"] == latest_end),
            key=lambda e: _period_days(e),
            default=quarterly[0],
        )
        try:
            mr_end_dt = _date.fromisoformat(ytd_cur["end"])
            mr_val = ytd_cur["val"]
            mr_end = ytd_cur["end"]
            cur_days = _period_days(ytd_cur)

            # Prior-year same fiscal portion. 52/53-week calendars drift a few days
            # year to year, so match the prior YTD entry FUZZILY: same duration
            # (±12d) and end date nearest to (mr_end − 1 year), within ±20 days —
            # rather than requiring an exact date match (which silently failed for
            # Coca-Cola / PepsiCo and dropped them to the prior full year).
            try:
                target_prior_end = mr_end_dt.replace(year=mr_end_dt.year - 1)
            except ValueError:
                target_prior_end = mr_end_dt.replace(year=mr_end_dt.year - 1, day=28)
            prior_candidates = [
                e for e in quarterly
                if e["end"] < mr_end
                and abs(_period_days(e) - cur_days) <= 12
                and abs((_date.fromisoformat(e["end"]) - target_prior_end).days) <= 20
            ]
            prior_q = min(
                prior_candidates,
                key=lambda e: abs((_date.fromisoformat(e["end"]) - target_prior_end).days),
                default=None,
            )
            prior_end = prior_q["end"] if prior_q else "?"
            # The annual base must be the fiscal year IMMEDIATELY before the current
            # quarter (end within ~400 days). Otherwise a concept the company
            # abandoned years ago (e.g. Fiserv's old revenue tag, last full year
            # 2021) would pair a 5-year-old base with recent quarters and produce a
            # garbage TTM that slips past the staleness guard (its label looks current).
            fy_prior = next(
                (e for e in sorted(
                    [a for a in valid if a.get("form") == "10-K" and 330 <= _period_days(a) <= 400],
                    key=lambda a: a.get("end", ""), reverse=True)
                 if e["end"] < mr_end and 0 < (mr_end_dt - _date.fromisoformat(e["end"])).days <= 400),
                None,
            )
            if prior_q and fy_prior:
                ttm = float(fy_prior["val"]) + float(mr_val) - float(prior_q["val"])
                detail = (
                    f"edgar_10q:ytd_reconstruction,"
                    f"FY({fy_prior['end']})={fy_prior['val']:,}+"
                    f"YTD({mr_end})={mr_val:,}-"
                    f"PriorYTD({prior_end})={prior_q['val']:,},concept={concept}"
                )
                return ttm, f"TTM via YTD recon ({mr_end})", mr_end, detail
        except Exception:
            pass

    # ── Path 3: most recent FULL-YEAR annual 10-K ────────────────────────────
    annual = [e for e in valid if e.get("form") == "10-K" and 330 <= _period_days(e) <= 400]
    if annual:
        best = annual[0]
        return float(best["val"]), f"FY ({best.get('end','')})", best.get("end", ""), f"edgar_10k:accession={best.get('accn','?')},concept={concept}"

    return None, "", "", f"Could not assemble TTM for {concept} (no contiguous quarters, YTD recon, or full-year annual)"


def _extract_ttm_cashflow(
    concept_data: dict,
    cik: str,
    concept: str,
) -> tuple[Optional[float], str, str, str]:
    """
    TTM assembly for cash flow items, which EDGAR reports as YTD cumulative.

    Strategy (in order):
    1. 4 individual single-quarter entries (rare for cash flows but possible)
    2. YTD reconstruction: TTM = FY_prior + most_recent_10Q - prior_year_same_period_10Q
    3. Most recent annual 10-K
    """
    from datetime import date as _date

    units = concept_data.get("units", {})
    usd_entries = units.get("USD", [])
    if not usd_entries:
        return None, "", "", f"No USD units for {concept}"

    valid = [e for e in usd_entries if e.get("form") in ("10-Q", "10-K") and e.get("val") is not None]
    if not valid:
        return None, "", "", "No 10-Q or 10-K entries"

    valid.sort(key=lambda e: e.get("end", ""), reverse=True)

    # Path 1: 4 individual quarters — must span ~12 months (not 4 Q1s from different years)
    q_single = [e for e in valid if e.get("form") == "10-Q" and _is_single_quarter(e)]
    if len(q_single) >= 4:
        seen_ends: set = set()
        selected = []
        for e in q_single:
            if e["end"] not in seen_ends:
                seen_ends.add(e["end"])
                selected.append(e)
            if len(selected) == 4:
                break
        if len(selected) == 4:
            # Validate: oldest and newest end dates should be within ~13 months
            try:
                from datetime import date as _date_cls
                newest = _date_cls.fromisoformat(selected[0]["end"])
                oldest = _date_cls.fromisoformat(selected[3]["end"])
                span_days = (newest - oldest).days
                if span_days <= 400:  # ~13 months — genuine consecutive quarters
                    ttm = sum(e["val"] for e in selected)
                    accessions = [e.get("accn", "?") for e in selected]
                    return ttm, f"TTM ({selected[0]['end']})", selected[0]["end"], f"edgar_10q:individual_quarters,concept={concept}"
                # else: span > 13 months means we're picking same-quarter from different years → fall through
            except Exception:
                pass

    # Path 2: YTD reconstruction
    # Most recent 10-Q (any period) — the starting point
    quarterly = [e for e in valid if e.get("form") == "10-Q" and e.get("start") and e.get("end")]
    if quarterly:
        most_recent_q = quarterly[0]
        mr_start = most_recent_q["start"]
        mr_end = most_recent_q["end"]
        mr_val = most_recent_q["val"]

        try:
            mr_end_dt = _date.fromisoformat(mr_end)
            mr_start_dt = _date.fromisoformat(mr_start)

            # Prior year same period: shift both dates back one year
            def shift_year(d: _date, delta: int) -> str:
                try:
                    return d.replace(year=d.year + delta).isoformat()
                except ValueError:
                    # Feb 29 edge case
                    return d.replace(year=d.year + delta, day=28).isoformat()

            prior_end = shift_year(mr_end_dt, -1)
            prior_start = shift_year(mr_start_dt, -1)

            # Find matching prior-year same-period 10-Q (exact start+end match)
            prior_q = next(
                (e for e in quarterly if e["end"] == prior_end and e["start"] == prior_start),
                None
            )

            # Most recent FULL-YEAR 10-K immediately preceding the current quarter
            # (end within ~400 days) — never a stale base from an abandoned concept.
            annual_entries = sorted(
                [e for e in valid if e.get("form") == "10-K" and 330 <= _period_days(e) <= 400],
                key=lambda e: e.get("end", ""), reverse=True
            )
            fy_prior = next(
                (e for e in annual_entries
                 if e["end"] < mr_end and 0 < (mr_end_dt - _date.fromisoformat(e["end"])).days <= 400),
                None
            )

            if prior_q and fy_prior:
                ttm = float(fy_prior["val"]) + float(mr_val) - float(prior_q["val"])
                detail = (
                    f"edgar_10q:ytd_reconstruction,"
                    f"FY({fy_prior['end']})={fy_prior['val']:,}+"
                    f"YTD({mr_end})={mr_val:,}-"
                    f"PriorYTD({prior_end})={prior_q['val']:,},"
                    f"concept={concept}"
                )
                return ttm, f"TTM via YTD recon ({mr_end})", mr_end, detail
        except Exception:
            pass

    # Path 3: annual fallback — full-year (~365d) entries only, never a stray quarter
    annual = [e for e in valid if e.get("form") == "10-K" and 330 <= _period_days(e) <= 400]
    if annual:
        best = annual[0]
        return float(best["val"]), f"FY ({best['end']})", best["end"], f"edgar_10k:annual_fallback,concept={concept}"

    return None, "", "", f"All TTM paths failed for {concept}"


def _fetch_latest_balance_sheet_value(
    cik: str,
    concepts: list[str],
    field_name: str,
) -> DataPoint:
    """
    For balance-sheet items (cash, debt, shares): fetch the single most recent
    point-in-time value from 10-Q or 10-K.
    """
    failures = []
    anchor = _get_anchor_period(cik)
    for concept in concepts:
        data = _fetch_concept(cik, concept)
        if not data:
            failures.append(f"{concept}: HTTP error or not found")
            continue
        units = data.get("units", {})
        entries = units.get("USD", units.get("shares", []))
        if not entries:
            failures.append(f"{concept}: no USD/shares units")
            continue
        # For balance sheet: take the entry with the latest end date from 10-Q/10-K
        filed = [e for e in entries if e.get("form") in ("10-Q", "10-K") and e.get("val") is not None]
        if not filed:
            failures.append(f"{concept}: no 10-Q/10-K entries")
            continue
        filed.sort(key=lambda e: e.get("end", ""), reverse=True)
        best = filed[0]
        stale = _staleness_reason(best.get("end", ""), anchor, STALENESS_BALANCE_MONTHS)
        if stale:
            failures.append(f"{concept}: {stale}")
            continue
        return make_dp(
            value=float(best["val"]),
            source="edgar_10q" if best.get("form") == "10-Q" else "edgar_10k",
            source_detail=f"edgar:{best.get('form')},accession={best.get('accn','?')},concept={concept}",
            period_label=f"As of {best.get('end','')}",
            period_end=best.get("end", ""),
        )
    return na_dp(f"All EDGAR concepts failed for {field_name}: " + " | ".join(failures))


def _fetch_ttm_income_statement(
    cik: str,
    concepts: list[str],
    field_name: str,
    cashflow: bool = False,
) -> DataPoint:
    """Fetch TTM value. Set cashflow=True for OCF/CapEx to use YTD-aware assembly."""
    failures = []
    extractor = _extract_ttm_cashflow if cashflow else _extract_ttm_quarters
    anchor = _get_anchor_period(cik)
    for concept in concepts:
        data = _fetch_concept(cik, concept)
        if not data:
            failures.append(f"{concept}: HTTP error or not found")
            continue
        val, period_label, period_end, detail = extractor(data, cik, concept)
        if val is not None:
            stale = _staleness_reason(period_end, anchor, STALENESS_FLOW_MONTHS)
            if stale:
                failures.append(f"{concept}: {stale}")
                continue
            return make_dp(
                value=float(val),
                source="edgar_10q",
                source_detail=detail,
                period_label=period_label,
                period_end=period_end,
            )
        failures.append(f"{concept}: {detail}")
    return na_dp(f"All EDGAR concepts failed for {field_name}: " + " | ".join(failures))


# ── Public fetch functions ────────────────────────────────────────────────────

def fetch_revenue_ttm(cik: str) -> DataPoint:
    return _fetch_ttm_income_statement(cik, REVENUE_CONCEPTS, "revenue_ttm")


def fetch_revenue_annual_pair(cik: str) -> tuple[DataPoint, DataPoint]:
    """Return (most_recent_FY, prior_FY) annual revenue as a matched pair.

    Growth is computed FY-over-FY from this pair so both endpoints are the same
    fiscal-period length and month — avoiding the old bug of comparing a TTM
    (e.g. ending Mar-2026) against a mismatched annual (e.g. FY ending Jun-2024).
    """
    # Build a (cur, prior) candidate from EACH concept, then pick the candidate
    # whose most-recent fiscal year-end is NEWEST. Picking the first concept with
    # ≥2 annuals (the old behaviour) breaks when a company ABANDONS a revenue
    # concept: e.g. NVIDIA stopped tagging RevenueFromContractWithCustomer-
    # ExcludingAssessedTax after FY2023 and moved to Revenues, but the old concept
    # still carries its FY2021–FY2023 filings — so the first-match returned a
    # 3-year-stale pair (FY2023 vs FY2021) and a wrong growth rate. The TTM path
    # already guards against this via staleness; mirror it here by preferring the
    # freshest concept. (Each candidate stays WITHIN one concept, so a year's
    # gross/net basis is never mixed across the pair.)
    candidates = []  # (cur_end, cur_entry, prior_entry, concept)
    for concept in REVENUE_CONCEPTS:
        data = _fetch_concept(cik, concept)
        if not data:
            continue
        units = data.get("units", {}).get("USD", [])
        # Keep only full-year (~365d) 10-K entries, dedupe by period-end. A later
        # 10-K can RESTATE a prior year onto a new basis (e.g. Kimberly-Clark
        # recasting FY2024 to continuing operations after divesting its IFP unit),
        # so prefer the MOST-RECENTLY-FILED value for each fiscal year-end —
        # otherwise YoY growth compares mismatched bases (a −18% mirage vs the
        # real −2% continuing-ops decline).
        full_year: dict = {}
        for e in units:
            if e.get("form") != "10-K" or e.get("val") is None:
                continue
            if not (330 <= _period_days(e) <= 400):
                continue
            end = e.get("end", "")
            if not end:
                continue
            prev = full_year.get(end)
            if prev is None or e.get("filed", "") > prev.get("filed", ""):
                full_year[end] = e
        annual = [full_year[k] for k in sorted(full_year, reverse=True)]
        if len(annual) >= 2:
            candidates.append((annual[0].get("end", ""), annual[0], annual[1], concept))

    if candidates:
        # Newest most-recent-FY wins (concept-order breaks ties for stability).
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, cur, prior, concept = candidates[0]
        cur_dp = make_dp(
            value=float(cur["val"]), source="edgar_10k",
            source_detail=f"edgar_10k:accession={cur.get('accn','?')},concept={concept}",
            period_label=f"FY ({cur.get('end','')})", period_end=cur.get("end", ""),
        )
        prior_dp = make_dp(
            value=float(prior["val"]), source="edgar_10k",
            source_detail=f"edgar_10k:accession={prior.get('accn','?')},concept={concept}",
            period_label=f"FY prior ({prior.get('end','')})", period_end=prior.get("end", ""),
        )
        return cur_dp, prior_dp
    na = na_dp("EDGAR: could not find two full-year 10-K revenue entries for FY-over-FY growth")
    return na, na


def fetch_gross_profit(cik: str) -> DataPoint:
    return _fetch_ttm_income_statement(cik, GROSS_PROFIT_CONCEPTS, "gross_profit")

def fetch_cost_of_revenue(cik: str) -> DataPoint:
    """Cost of revenue TTM — used to derive gross profit (Revenue − Cost) when
    the GrossProfit concept is untagged or stale (e.g. JKHY, FOUR)."""
    return _fetch_ttm_income_statement(cik, COST_OF_REVENUE_CONCEPTS, "cost_of_revenue")

def fetch_operating_income(cik: str) -> DataPoint:
    return _fetch_ttm_income_statement(cik, OPERATING_INCOME_CONCEPTS, "operating_income")

def fetch_net_income(cik: str) -> DataPoint:
    return _fetch_ttm_income_statement(cik, NET_INCOME_CONCEPTS, "net_income_ttm")

def fetch_operating_cash_flow(cik: str) -> DataPoint:
    return _fetch_ttm_income_statement(cik, OCF_CONCEPTS, "operating_cash_flow", cashflow=True)

def fetch_capex(cik: str) -> DataPoint:
    """CapEx is always negative in EDGAR cash flow statements; we return the absolute value."""
    dp = _fetch_ttm_income_statement(cik, CAPEX_CONCEPTS, "capex", cashflow=True)
    if dp.value is not None and dp.value < 0:
        dp = dp.model_copy(update={"value": abs(dp.value)})
    return dp

def fetch_da(cik: str) -> DataPoint:
    """Fetch Depreciation & Amortization TTM for computing EBITDA = OpIncome + D&A."""
    return _fetch_ttm_income_statement(cik, DA_CONCEPTS, "depreciation_amortization", cashflow=True)

def fetch_total_cash(cik: str) -> DataPoint:
    return _fetch_latest_balance_sheet_value(cik, CASH_CONCEPTS, "total_cash")

def fetch_total_debt(cik: str) -> DataPoint:
    return _fetch_latest_balance_sheet_value(cik, DEBT_CONCEPTS, "total_debt")

def fetch_shares_outstanding(cik: str) -> DataPoint:
    return _fetch_latest_balance_sheet_value(cik, SHARES_CONCEPTS, "shares_outstanding")
