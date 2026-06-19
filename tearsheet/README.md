# 📄 Tearsheet

**Deterministic sector valuation dashboards from primary-source data.**

Tearsheet builds a one-page valuation comparison for an entire sector. For each
company it pulls financial statements from **SEC EDGAR XBRL**, market data from
**Yahoo Finance**, computes ~20 valuation metrics with pure functions, and
renders a polished, self-contained HTML dashboard. Every figure is traceable to
its filing/period, and every `N/A` carries an explicit reason.

It is **deterministic** (same inputs → same output, no LLM in the data path) and
**keyless** by default — EDGAR and Yahoo Finance need no API keys, so you can
clone and run in two minutes.

> **Not investment advice.** Research and educational use only.

<!-- Add a screenshot here after running: docs/preview.png
![Tearsheet — Payments sector](docs/preview.png) -->

📊 **Example output:** [`examples/payments_tearsheet.html`](examples/payments_tearsheet.html) — open it in a browser.

---

## Quickstart

```bash
cd tearsheet
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run a whole sector (companies defined in sectors/payments.yaml)
.venv/bin/python pipeline/run.py --sector payments
# → output/Payments/payments_valuation.html
```

Run a subset, or open the result automatically:

```bash
.venv/bin/python pipeline/run.py --sector payments --tickers FOUR --tickers TOST --open-browser
```

## Add your own sector

Sectors are declarative — **no code changes**. Copy the example and edit the
company list:

```bash
cp sectors/payments.yaml sectors/cloud.yaml
# edit sectors/cloud.yaml, then:
.venv/bin/python pipeline/run.py --sector cloud
```

Each company needs only `name`, `focus`, `exchange`, `currency`. Add a `cik:` or
`yfinance_ticker:` override only when auto-lookup picks the wrong entity or Yahoo
uses a different symbol. The file is fully commented.

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
  Yahoo Finance; foreign issuers without EDGAR XBRL fall back to a public source.
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
- Non-US issuers have thinner coverage and lean on the scrape fallback.
- yfinance is an unofficial Yahoo Finance wrapper; market data can occasionally
  lag or gap. An optional pre-computed-data adapter is on the roadmap.

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
