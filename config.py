"""
Central configuration and guardrails for the Amazon growth engine MVP.

Everything tunable lives here so the operating policy is visible in one place
rather than buried in the engines. In a production system this would be per-client
configuration stored in a database, since margin structure and risk appetite differ
wildly between sellers.
"""

# --- Simulation ---
SIM_DAYS = 90
RANDOM_SEED = 11
KEYWORDS_PER_SKU = 4
IMPRESSION_SCALE = 2800          # scales synthetic ad impression volume

# --- Marketplace ---
# Amazon Ads runs in each marketplace's own currency: amazon.in bids and reports in
# INR, amazon.com in USD. There is no cross-currency bidding. Everything below is
# denominated in the marketplace currency.
MARKETPLACE = "IN"
CURRENCY = "INR"
CURRENCY_SYMBOL = "Rs "

# --- Amazon.in economics ---
# India's fee structure differs from the US in one way that matters a lot: since
# March 2026 the referral fee is 0% on a very large set of categories for products
# priced under Rs 1,000, with reduced rates above that. So the referral fee is a
# function of price, not a flat percentage - see referral_fee() below.
# Verify current rates for your category at https://sell.amazon.in/fees-and-pricing
REFERRAL_FEE_RATE = 0.085        # representative rate ABOVE the zero-fee threshold
ZERO_REFERRAL_FEE_BELOW = 1000.0 # Rs - no referral fee under this price
CLOSING_FEE_PER_UNIT = 25.0      # flat per-unit closing fee (varies by value band)
FBA_FEE_PER_UNIT = 70.0          # weight handling, standard size, national rate
STORAGE_FEE_PER_UNIT_DAY = 0.40  # monthly storage, expressed per unit per day


def referral_fee(price: float) -> float:
    """Amazon.in referral fee for one unit at this price.

    The cliff at Rs 1,000 is real money: a product at Rs 999 pays no referral fee at
    all, while one at Rs 1,001 pays ~8.5%. Because the advertising engine derives its
    ACOS target from unit margin, it picks this up automatically and bids more
    aggressively on the sub-Rs 1,000 products - no configuration needed.
    """
    if price < ZERO_REFERRAL_FEE_BELOW:
        return 0.0
    return price * REFERRAL_FEE_RATE

# --- PPC guardrails ---
MAX_BID_CHANGE_PCT = 0.15        # never move a bid more than 15% in one adjustment
MIN_BID = 1.00                   # amazon.in minimum bid is Rs 1 per click
MAX_BID = 150.00                 # headroom over typical Rs 7-35 Sponsored Products CPCs
MIN_WASTE_THRESHOLD = 50.0       # Rs - floor for "this keyword has wasted enough"
MIN_CLICKS_TO_ACT = 15           # statistical-significance floor before touching a bid
MIN_CLICKS_TO_NEGATE = 25        # higher bar before killing a keyword outright
PPC_ROLLING_WINDOW = 7           # days of trailing data behind each bid decision
TARGET_MARGIN_SHARE = 0.55       # spend at most 55% of unit margin on ads
ACOS_HIGH_TOLERANCE = 1.15       # act when ACOS exceeds target by 15%
ACOS_LOW_TOLERANCE = 0.75        # act when ACOS is 25% under target (room to grow)
MAX_BID_RAISE_PCT = 0.08         # raise slowly, cut fast - see bid_up in engines/ppc.py
HARVEST_MIN_ORDERS = 3           # orders on a broad keyword before harvesting to exact

# --- Pricing guardrails ---
MAX_PRICE_CHANGE_PCT = 0.05      # max 5% price move per change - avoids spooking the buy box
MIN_NET_MARGIN_PCT = 0.12        # hard floor: never price below 12% net margin
PRICE_CHANGE_COOLDOWN_DAYS = 3   # avoid price thrash
BUY_BOX_PRICE_TOLERANCE = 1.02   # lose the buy box once we're >2% above the competitor

# --- Inventory guardrails ---
FORECAST_WINDOW = 21             # days of clean (non-stockout) demand behind the forecast
SAFETY_DAYS = 10                 # buffer on top of supplier lead time
TARGET_DAYS_COVER = 45           # restock up to this much cover
CRITICAL_STOCK_DAYS = 7          # below this, ads throttle hard
LOW_STOCK_DAYS = 14              # below this, ads throttle moderately
OVERSTOCK_DAYS = 90              # above this, clear inventory via price + ads
MIN_ORDER_UNITS = 50             # don't place trivially small POs
MAX_ORDER_UNITS = 6000           # cash-flow guardrail on a single PO

# --- Listing / SEO ---
LISTING_WINDOW = 14              # days of traffic data behind a listing quality score
LISTING_MIN_IMPRESSIONS = 600    # don't judge a listing on thin data
LISTING_SCORE_FLOOR = 0.45       # below this, flag for rewrite
LISTING_REWRITE_COOLDOWN_DAYS = 30
LISTING_REINDEX_DAYS = 5         # Amazon takes days to reindex a changed listing
LISTING_QUALITY_LIFT = 0.12      # modelled effect of a successful rewrite
BENCHMARK_CTR = 0.0055           # category benchmark click-through rate
BENCHMARK_CVR = 0.09             # category benchmark conversion rate

# --- Manual baseline policy (the "typical competent but manual seller") ---
MANUAL_FORECAST_WINDOW = 14
MANUAL_REORDER_LEAD_TIME_SHARE = 0.9  # knows the lead time, but carries no safety stock
MANUAL_ORDER_COVER_DAYS = 60          # orders a round "two months" each time
