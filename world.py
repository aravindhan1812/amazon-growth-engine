"""
Simulated Amazon marketplace - the "hidden truth" of this MVP.

This module owns things a real seller can never observe directly: true demand curves,
price elasticity, per-keyword purchase intent, and how listing quality actually affects
click-through and conversion. The optimisation engines never import from this module.
They only read `MetricsStore`, which is deliberately restricted to fields you can
genuinely pull from Amazon's APIs.

That boundary is the point. If an engine performs well here it is because it reasoned
correctly from observable signals - not because it peeked at the answer key. When you
swap this module for the real Amazon APIs, the engines should not need to change.

Modelled dynamics:
  - price elasticity (cheaper -> more units, with diminishing margin)
  - a competitor whose price moves on a mean-reverting random walk
  - buy box ownership, lost when priced above the competitor's threshold
  - buy box loss craters conversion (~8x) while ads keep spending - a classic profit leak
  - stockouts stop ad serving entirely and decay organic rank, which recovers slowly
  - listing quality lifting both CTR and conversion, with a reindex delay after changes
"""

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config


@dataclass
class Keyword:
    keyword_id: str
    text: str
    match_type: str          # broad | exact
    relevance: float         # HIDDEN: drives impression volume and CTR
    intent: float            # HIDDEN: drives conversion rate
    bid: float
    active: bool = True
    harvested: bool = False


@dataclass
class SKU:
    sku_id: str
    title: str
    price: float
    reference_price: float
    unit_cost: float
    elasticity: float        # HIDDEN
    base_demand: float       # HIDDEN
    listing_quality: float   # HIDDEN (engines infer it from CTR/CVR)
    competitor_price: float
    competitor_base: float
    lead_time_days: int
    on_hand: int
    keywords: List[Keyword] = field(default_factory=list)
    inbound: List[Tuple[int, int]] = field(default_factory=list)  # (arrival_day, units)
    rank_health: float = 1.0
    has_buy_box: bool = True
    bid_multiplier: float = 1.0       # set by the orchestrator, not by the PPC engine
    pending_listing_boost: Optional[Tuple[int, float]] = None
    last_rewrite_day: int = -999
    last_price_change_day: int = -999


# A deliberately varied catalogue so every layer has something real to do:
#   B  - weak listing, so the listing engine has a genuine problem to find
#   C  - 45-day lead time, so inventory planning actually matters
#   D  - thin margin and high elasticity, where a flat 25% ACOS target destroys profit
#   F  - aggressive competitor, so buy box defence matters
CATALOG_SPEC = [
    dict(sku_id="SKU-A", title="Kids Rain Jacket",      price=34.99, unit_cost=11.50,
         elasticity=1.6, base_demand=22, listing_quality=0.72, lead_time=35, comp_mult=1.06),
    dict(sku_id="SKU-B", title="Cotton Bath Towel Set", price=24.99, unit_cost=9.80,
         elasticity=2.1, base_demand=30, listing_quality=0.42, lead_time=28, comp_mult=1.04),
    dict(sku_id="SKU-C", title="Dog Puzzle Feeder",     price=19.99, unit_cost=6.20,
         elasticity=1.3, base_demand=14, listing_quality=0.80, lead_time=45, comp_mult=1.10),
    dict(sku_id="SKU-D", title="Toddler Puzzle Set",    price=16.49, unit_cost=7.40,
         elasticity=2.4, base_demand=26, listing_quality=0.63, lead_time=30, comp_mult=1.03),
    dict(sku_id="SKU-E", title="Memory Foam Pillow",    price=42.00, unit_cost=15.00,
         elasticity=1.1, base_demand=11, listing_quality=0.68, lead_time=40, comp_mult=1.08),
    dict(sku_id="SKU-F", title="Reusable Snack Bags",   price=13.99, unit_cost=4.10,
         elasticity=2.0, base_demand=35, listing_quality=0.58, lead_time=25, comp_mult=0.99),
]


def build_catalog() -> List[SKU]:
    """Deterministic catalogue - identical for every regime, so comparisons are fair."""
    rng = random.Random(config.RANDOM_SEED)
    skus: List[SKU] = []
    for spec in CATALOG_SPEC:
        keywords = []
        for k in range(config.KEYWORDS_PER_SKU):
            keywords.append(Keyword(
                keyword_id=f"{spec['sku_id']}-kw{k + 1}",
                text=f"{spec['title'].lower()} term {k + 1}",
                match_type="broad" if k == 0 else "exact",
                relevance=rng.uniform(0.35, 0.95),
                intent=rng.uniform(0.15, 0.90),
                bid=round(rng.uniform(0.55, 1.25), 2),
            ))
        competitor_price = round(spec["price"] * spec["comp_mult"], 2)
        skus.append(SKU(
            sku_id=spec["sku_id"],
            title=spec["title"],
            price=spec["price"],
            reference_price=spec["price"],
            unit_cost=spec["unit_cost"],
            elasticity=spec["elasticity"],
            base_demand=spec["base_demand"],
            listing_quality=spec["listing_quality"],
            competitor_price=competitor_price,
            competitor_base=competitor_price,
            lead_time_days=spec["lead_time"],
            on_hand=int(spec["base_demand"] * 38),
            keywords=keywords,
        ))
    return skus


def day_rng(sku_index: int, day: int) -> random.Random:
    """One RNG stream per (SKU, day).

    This is what makes the A/B honest: both the manual and automated runs draw the
    *same* market noise on the same day for the same SKU, so any difference in outcome
    comes from the decisions, not from luck. Each call site below draws a fixed number
    of values in a fixed order, regardless of which branch is taken, to keep the two
    runs aligned.
    """
    return random.Random((config.RANDOM_SEED * 1000003) ^ (sku_index * 7919) ^ (day * 104729))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def simulate_day(sku: SKU, sku_index: int, day: int) -> Dict:
    """Advance one SKU by one day and return the observable record for that day."""
    rng = day_rng(sku_index, day)

    # 1. Receive any inbound shipments that have arrived
    arrived = 0
    still_inbound = []
    for arrival_day, units in sku.inbound:
        if arrival_day <= day:
            arrived += units
        else:
            still_inbound.append((arrival_day, units))
    sku.inbound = still_inbound
    sku.on_hand += arrived

    # 2. Apply any listing rewrite that has finished reindexing
    if sku.pending_listing_boost is not None and day >= sku.pending_listing_boost[0]:
        sku.listing_quality = min(1.0, sku.listing_quality + sku.pending_listing_boost[1])
        sku.pending_listing_boost = None

    # 3. Competitor price: mean-reverting random walk around its own base
    drift = rng.gauss(0, 0.012)
    pull = (sku.competitor_base - sku.competitor_price) * 0.05
    sku.competitor_price = round(_clamp(
        sku.competitor_price * (1 + drift) + pull,
        sku.competitor_base * 0.82,
        sku.competitor_base * 1.18,
    ), 2)

    # 4. Buy box: lost if we price above the competitor's tolerance, or if we're out of stock
    sku.has_buy_box = sku.on_hand > 0 and sku.price <= sku.competitor_price * config.BUY_BOX_PRICE_TOLERANCE
    buy_box_factor = 1.0 if sku.has_buy_box else 0.12

    quality_mult = 0.6 + 0.8 * sku.listing_quality
    seasonal = 1.0 + 0.12 * math.sin(2 * math.pi * day / 90.0)

    # 5. Organic demand
    price_effect = (sku.reference_price / sku.price) ** sku.elasticity
    lam_organic = (sku.base_demand * price_effect * quality_mult * seasonal
                   * sku.rank_health * buy_box_factor)
    organic_noise = rng.gauss(1.0, 0.22)
    organic_demand = max(0, int(round(lam_organic * max(0.15, organic_noise))))

    # 6. Paid demand, keyword by keyword
    price_competitiveness = _clamp((sku.competitor_price / sku.price) ** 1.4, 0.45, 1.5)
    on_hand_at_open = sku.on_hand
    keyword_records: Dict[str, Dict] = {}
    paid_demand = 0
    ad_spend = 0.0
    total_impressions = 0
    total_clicks = 0

    for kw in sku.keywords:
        # Draw noise unconditionally so RNG streams stay aligned across regimes
        n_impr = rng.gauss(1.0, 0.15)
        n_ctr = rng.gauss(1.0, 0.25)
        n_cpc = rng.uniform(0.55, 0.88)
        n_conv = rng.gauss(0, 1)

        eligible = kw.active and on_hand_at_open > 0 and sku.bid_multiplier > 0
        if not eligible:
            keyword_records[kw.keyword_id] = {
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "orders": 0, "sales": 0.0, "bid": kw.bid,
            }
            continue

        effective_bid = min(config.MAX_BID, kw.bid * sku.bid_multiplier)
        exposure = min(effective_bid, 3.0) * kw.relevance * (0.7 + 0.3 * sku.listing_quality)
        impressions = max(0, int(max(0.2, n_impr) * config.IMPRESSION_SCALE * exposure))

        ctr = (0.006 * kw.relevance + 0.002) * quality_mult
        clicks = min(impressions, max(0, int(round(impressions * ctr * max(0.2, n_ctr)))))

        cpc = effective_bid * n_cpc
        spend = round(clicks * cpc, 2)

        cvr = (0.02 + 0.13 * kw.intent) * price_competitiveness * quality_mult * buy_box_factor
        expected_orders = clicks * cvr
        orders = max(0, int(round(expected_orders + n_conv * math.sqrt(max(expected_orders, 0.5)))))
        orders = min(orders, clicks)

        keyword_records[kw.keyword_id] = {
            "impressions": impressions, "clicks": clicks, "spend": spend,
            "orders": orders, "sales": 0.0, "bid": kw.bid,
        }
        paid_demand += orders
        ad_spend += spend
        total_impressions += impressions
        total_clicks += clicks

    # 7. Demand is capped by what we can actually ship
    total_demand = organic_demand + paid_demand
    units_sold = min(total_demand, max(0, sku.on_hand))
    lost_units = total_demand - units_sold

    if total_demand > 0:
        paid_sold = min(paid_demand, int(round(units_sold * paid_demand / total_demand)))
        organic_sold = units_sold - paid_sold
    else:
        paid_sold = organic_sold = 0

    # Attribute ad sales back to keywords, scaled down if we ran out of stock mid-day
    fulfil_ratio = (paid_sold / paid_demand) if paid_demand > 0 else 0.0
    for record in keyword_records.values():
        if fulfil_ratio < 1.0:
            record["orders"] = int(record["orders"] * fulfil_ratio)
        record["sales"] = round(record["orders"] * sku.price, 2)

    sku.on_hand = max(0, sku.on_hand - units_sold)

    # 8. Financials
    revenue = round(units_sold * sku.price, 2)
    ad_sales = round(paid_sold * sku.price, 2)
    cogs = round(units_sold * sku.unit_cost, 2)
    referral_fee = round(revenue * config.REFERRAL_FEE_RATE, 2)
    fba_fee = round(units_sold * config.FBA_FEE_PER_UNIT, 2)
    storage_fee = round(sku.on_hand * config.STORAGE_FEE_PER_UNIT_DAY, 2)
    fees = round(referral_fee + fba_fee + storage_fee, 2)
    ad_spend = round(ad_spend, 2)
    gross_profit = round(revenue - cogs - fees - ad_spend, 2)

    # 9. Organic rank decays while out of stock and recovers slowly once back
    if sku.on_hand <= 0:
        sku.rank_health = max(0.45, sku.rank_health * 0.93)
    else:
        sku.rank_health = min(1.0, sku.rank_health * 1.03)

    return {
        "day": day,
        "sku_id": sku.sku_id,
        "price": round(sku.price, 2),
        "competitor_price": sku.competitor_price,
        "has_buy_box": sku.has_buy_box,
        "on_hand_start": on_hand_at_open,
        "on_hand_end": sku.on_hand,
        "inbound_units": sum(u for _, u in sku.inbound),
        "received_units": arrived,
        "organic_units": organic_sold,
        "paid_units": paid_sold,
        "units_sold": units_sold,
        "lost_units": lost_units,
        "censored": lost_units > 0 or on_hand_at_open <= 0,
        "revenue": revenue,
        "ad_sales": ad_sales,
        "ad_spend": ad_spend,
        "cogs": cogs,
        "fees": fees,
        "gross_profit": gross_profit,
        "impressions": total_impressions,
        "clicks": total_clicks,
        "keywords": keyword_records,
    }
