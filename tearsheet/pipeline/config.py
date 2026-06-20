import os

PIPELINE_VERSION = "1.0.0"

# SEC requires a User-Agent that identifies the requester with a contact.
# Read it from the environment so each user declares their own identity
# (cloners should `export EDGAR_USER_AGENT="YourApp your@email.com"`); the
# fallback is generic and contains no personal address.
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT",
    "Tearsheet/1.0 (open-source equity research; set EDGAR_USER_AGENT to your contact)",
)
EDGAR_RATE_LIMIT_SLEEP = 0.12   # seconds between EDGAR calls (stays under 10 req/sec)
EDGAR_BASE_URL = "https://data.sec.gov"

# ── Staleness guards ──────────────────────────────────────────────────────────
# A company can stop tagging a metric under one XBRL concept and switch to
# another; the old concept then keeps returning years-old data. We reject any
# selected value whose period ends more than these many months before the
# company's most recent filing period (the "anchor"), and fall through to the
# next concept or N/A-with-reason. Catches e.g. PRTH net income stuck at 2021,
# FI long-term debt stuck at 2012, JKHY gross profit stuck at 2017.
STALENESS_FLOW_MONTHS = 18      # TTM / income-statement / cash-flow items
STALENESS_BALANCE_MONTHS = 12   # point-in-time balance-sheet items (cash, debt, shares)

# EDGAR GAAP concept name aliases — tried in order until one returns data
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomer",
]
GROSS_PROFIT_CONCEPTS = ["GrossProfit"]
# Cost of revenue — used to derive gross profit when GrossProfit is not tagged (e.g. JKHY, FOUR)
COST_OF_REVENUE_CONCEPTS = [
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfServices",
]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
]
OCF_CONCEPTS = ["NetCashProvidedByUsedInOperatingActivities"]
CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CapitalExpendituresIncurredButNotYetPaid",
    "PaymentsForCapitalImprovements",
]
CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsAndShortTermInvestments",
    "CashAndCashEquivalentsAndShortTermInvestments",
]
DEBT_CONCEPTS = [
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "DebtLongtermAndShorttermCombinedAmount",
    "DebtCurrent",
]
SHARES_CONCEPTS = [
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]
EBITDA_CONCEPTS = ["EarningsBeforeInterestTaxesDepreciationAndAmortization"]  # rarely tagged; usually computed

DA_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "AmortizationOfIntangibleAssets",
]

# ── Color-coding thresholds ───────────────────────────────────────────────────
# Each entry is (green_max, yellow_max); values above yellow_max are red
# For metrics where lower is better:
EV_REVENUE_THRESHOLDS   = (3.0, 7.0)    # <3 green, 3-7 yellow, >7 red
EV_EBITDA_THRESHOLDS    = (15.0, 30.0)
EV_FCF_THRESHOLDS       = (15.0, 30.0)
PE_TTM_THRESHOLDS       = (15.0, 30.0)
FWD_PE_THRESHOLDS       = (15.0, 30.0)
NET_DEBT_EBITDA_THRESH  = (2.0, 4.0)

# For metrics where higher is better (green_min, yellow_min; below yellow_min is red)
RULE_OF_40_THRESHOLDS   = (40.0, 20.0)  # >=40 green, 20-39 yellow, <20 red
GROSS_MARGIN_THRESHOLDS = (60.0, 40.0)
EBITDA_MARGIN_THRESHOLDS = (20.0, 10.0)
FCF_MARGIN_THRESHOLDS   = (20.0, 10.0)

# Sentinel value used in data-* attributes for N/A (so sort pushes them to bottom)
NA_SENTINEL = 999

# Source fallback order for each field category
MARKET_DATA_SOURCES  = ["yfinance", "stockanalysis_scrape"]
FINANCIAL_SOURCES    = ["edgar_10q", "edgar_10k", "stockanalysis_scrape"]
ESTIMATE_SOURCES     = ["yfinance", "stockanalysis_scrape"]  # forward EPS, growth estimates
