# Data sources

Tearsheet is built to be safe to run and share as open source. By default it
uses only two sources, both free and keyless. A third (scraping) is **opt-in**.

| Source | Used for | Status & terms | What you must do |
|---|---|---|---|
| **SEC EDGAR** (`data.sec.gov` XBRL API) | All financial-statement figures — revenue, EBITDA inputs, cash flow, cash, debt, shares | **Public domain.** U.S. government data, free to use and redistribute. SEC's [fair-access policy](https://www.sec.gov/os/webmaster-faq#developers) requires a descriptive `User-Agent` with a contact and ≤10 requests/sec. | Set `EDGAR_USER_AGENT` to your own contact (see below). The pipeline already rate-limits. |
| **Yahoo Finance** (via the `yfinance` library) | Market data & analyst estimates — price, shares, forward EPS, PEG | **Unofficial.** `yfinance` is a third-party wrapper, not an official API; data is for **personal / research use** and is not redistributed by this tool (we fetch live, we don't ship a Yahoo dataset). Values can occasionally lag or gap. | Nothing. Use for research, not redistribution. |
| **StockAnalysis.com** (HTML scrape) | Last-resort fallback for foreign issuers / EDGAR gaps | **Opt-in, OFF by default.** Scraping may violate their Terms of Service. Not run unless you pass `--use-scrape`. | Only enable if your use complies with their ToS — your responsibility. |

## Setting your EDGAR User-Agent

SEC asks every automated requester to identify themselves. Before running:

```bash
export EDGAR_USER_AGENT="YourName-or-App your@email.com"
```

If unset, a generic fallback is used (no personal address), but setting your own
contact is the courteous and compliant choice.

## Why no paid API by default

Keeping the default path **keyless** (EDGAR + Yahoo) means anyone can clone and
run in two minutes with no signup. An optional pre-computed-data adapter
(e.g. Financial Modeling Prep / Alpha Vantage) with EDGAR cross-verification is
on the roadmap as an *opt-in* enhancement, not a requirement.

> **Not investment advice.** Data is point-in-time and may contain errors; verify
> anything important against the primary filing (every figure links to its source).
