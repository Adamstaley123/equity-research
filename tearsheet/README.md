# 📄 Tearsheet

**Deterministic sector valuation dashboards from primary-source data.**

Tearsheet builds a one-page valuation comparison for an entire sector. For each
company it pulls financial statements from **SEC EDGAR XBRL**, market data from
**Yahoo Finance**, computes ~20 valuation metrics with pure functions, and
renders a polished, self-contained HTML dashboard. Every figure is traceable to
its filing/period, and every `N/A` carries an explicit reason.

It is **deterministic** (same inputs → same output, no LLM in the data path) and
**keyless** by default — EDGAR and Yahoo Finance need no API keys, so you can
clone and run in two minutes. Every figure links back to its **exact SEC filing**.

Ships with three sectors out of the box — **payments**, **semiconductors**, and
**consumer staples** — and running your own is a single command (just a ticker
list, no file needed). See [`DATA_SOURCES.md`](DATA_SOURCES.md) for the
(open-source-safe) data sources and terms.

> **Not investment advice.** Research and educational use only.

<!-- Add a screenshot here after running: docs/preview.png
![Tearsheet — Payments sector](docs/preview.png) -->

🔗 **[Live demo — Payments sector tearsheet](https://adamstaley123.github.io/equity-research/)**
&nbsp;·&nbsp; or open [`examples/payments_tearsheet.html`](examples/payments_tearsheet.html) locally.

---

## Quickstart

```bash
cd tearsheet
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# (Recommended) tell SEC who's calling — any descriptive string + contact:
export EDGAR_USER_AGENT="YourName your@email.com"

# Run a built-in sector (companies defined in sectors/<name>.yaml)
.venv/bin/python pipeline/run.py --sector payments
# → output/Payments/payments_valuation.html
# other built-ins: --sector semiconductors  |  --sector consumer_staples

# …or run ANY tickers with no file at all — name/industry/currency are
# auto-filled from Yahoo Finance:
.venv/bin/python pipeline/run.py --tickers NEE,DUK,SO,D,AEP --name "Utilities"
# → output/Utilities/utilities_valuation.html
```

By default only **SEC EDGAR + Yahoo Finance** are used (keyless, no scraping).
An optional StockAnalysis fallback for foreign issuers is **off** unless you pass
`--use-scrape` (see [`DATA_SOURCES.md`](DATA_SOURCES.md)).

Run a subset, or open the result automatically:

```bash
.venv/bin/python pipeline/run.py --sector payments --tickers FOUR --tickers TOST --open-browser
```

## Run your own sector

**There is no built-in screener — you choose the companies.** The "sector" is just
the list of tickers you give it. Two ways to do that:

**1. Quick — pass tickers on the command line (no file):**

```bash
.venv/bin/python pipeline/run.py --tickers NEE,DUK,SO,D,AEP --name "Utilities"
```

That's the whole thing. For each ticker the pipeline looks up the SEC entity by
exact ticker match against SEC's official company list, pulls financials from
EDGAR and market data from Yahoo, and **auto-fills the company name, business
description, exchange, and currency from Yahoo** — so you only type tickers.

**2. Curated — a YAML file (for a polished, repeatable sector):**

```bash
cp sectors/payments.yaml sectors/utilities.yaml
# edit the companies list, then:
.venv/bin/python pipeline/run.py --sector utilities
```

In a YAML, **every field except the ticker key is optional** (anything you omit is
auto-filled from Yahoo, same as above). A minimal file is just:

```yaml
name: Utilities
companies:
  NEE:
  DUK:
  SO:
```

Add `name` / `focus` to control the display text, or a `cik:` / `yfinance_ticker:`
override **only** when auto-lookup picks the wrong entity (rare — usually short,
ambiguous tickers) or Yahoo lists the symbol differently. `sectors/payments.yaml`
is fully commented as a reference.

## What it computes

| Group | Metrics |
|---|---|
| Size | Market cap, Enterprise value |
| Revenue | Revenue TTM, YoY growth (FY-over-FY) |
| Margins | Gross, EBITDA, FCF |
| EV multiples | EV/Revenue, EV/EBITDA, EV/FCF |
| Price multiples | P/E (TTM), Forward P/E, PEG, P/S, P/FCF |
| Quality | Net Debt/EBITDA, Rule of 40 |

Click any ticker in the dashboard to expand per-metric **sources, formulas, raw
inputs, and N/A reasons**. Sort by any column; toggle light/dark.

## How the numbers are built

- **Sources** — financials from SEC EDGAR XBRL (10-Q/10-K); market data from
  Yahoo Finance. Both are keyless and on by default. Foreign issuers without
  EDGAR XBRL need the opt-in `--use-scrape` fallback, otherwise their financials
  show `N/A` with a clear reason. Every value links to its exact filing/page.
- **TTM** — four most recent quarters, or YTD reconstruction for cash-flow items
  (`TTM = FY_prior + YTD_current − YTD_prior`).
- **Freshness guard** — every value is checked against the company's latest
  filing date. If a company stops tagging a metric under one XBRL concept, the
  stale value is rejected and the next concept is tried, or it goes `N/A` with a
  reason — no silently years-old numbers.
- **Enterprise value** — `EV = Market Cap + Total Debt − Total Cash`, computed
  from components (pre-computed EV fields have known stale-value bugs). Market
  cap = price × basic shares.
- **EBITDA** — `Operating Income + D&A` from EDGAR (op-income proxy when D&A is
  untagged/unreliable, and flagged).
- **Estimates** — Forward P/E and PEG are analyst consensus (marked `EST`).

### Known limitations

- Revenue basis differs across companies (net vs gross) — EV/Revenue isn't
  directly comparable across those; net-revenue names are tagged `net†`.
- Non-US issuers have no EDGAR XBRL; their financials are `N/A` unless you opt in
  to the StockAnalysis scrape (`--use-scrape`), which may be subject to that
  site's ToS — see [`DATA_SOURCES.md`](DATA_SOURCES.md).
- yfinance is an unofficial Yahoo Finance wrapper; market data can occasionally
  lag, gap, or spike. Headline market caps are price × shares from Yahoo — sanity
  check anything surprising against the linked filing. An optional pre-computed-data
  adapter with cross-verification is on the roadmap.

## Layout

```
tearsheet/
├── pipeline/
│   ├── run.py        CLI + orchestration
│   ├── sectors.py    Sector YAML loader
│   ├── config.py     EDGAR concepts, thresholds, staleness windows
│   ├── schema.py     Pydantic models (provenance on every field)
│   ├── compute.py    Ratio math (pure functions)
│   ├── render.py     Jinja2 rendering + summary stats
│   ├── fetchers/     edgar / yfinance / stockanalysis
│   └── templates/    dashboard.html.j2
├── sectors/          one YAML per sector (the company universe)
├── examples/         committed sample output
└── tests/            unit + smoke tests
```

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

## Roadmap

- [ ] Optional pre-computed-data adapter (Financial Modeling Prep / Alpha Vantage) with EDGAR cross-verification
- [ ] More sectors out of the box
- [ ] Historical / time-series snapshots
- [ ] Interactive "type your own tickers" web version

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](../LICENSE) · **Not investment advice.**
