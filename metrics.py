"""
The observability layer - everything the engines are allowed to see.

Each field here maps to something you can genuinely pull from Amazon:
  impressions/clicks/spend/orders   -> Advertising API, sponsored products reports
  units_sold / revenue              -> SP-API Orders + Business Reports
  price / competitor_price / buy box-> SP-API Product Pricing (getItemOffers)
  on_hand / inbound                 -> SP-API FBA Inventory (getInventorySummaries)

Note what is deliberately NOT here: true demand, elasticity, keyword intent, listing
quality. Real sellers cannot observe those, so the engines must infer them. `lost_units`
exists in the world model but is never exposed through the forecasting helpers below,
because a seller genuinely cannot measure the sales a stockout cost them.
"""

from typing import Dict, List

import config


class MetricsStore:
    def __init__(self):
        self.records: Dict[str, List[Dict]] = {}

    def record(self, rec: Dict) -> None:
        self.records.setdefault(rec["sku_id"], []).append(rec)

    def history(self, sku_id: str) -> List[Dict]:
        return self.records.get(sku_id, [])

    def window(self, sku_id: str, days: int) -> List[Dict]:
        return self.history(sku_id)[-days:]

    def rolling_sum(self, sku_id: str, days: int, field: str) -> float:
        return sum(r[field] for r in self.window(sku_id, days))

    def keyword_window(self, sku_id: str, keyword_id: str, days: int) -> Dict[str, float]:
        totals = {"impressions": 0, "clicks": 0, "spend": 0.0, "orders": 0, "sales": 0.0}
        for rec in self.window(sku_id, days):
            kw = rec["keywords"].get(keyword_id)
            if not kw:
                continue
            for key in totals:
                totals[key] += kw[key]
        return totals

    def clean_daily_units(self, sku_id: str, days: int) -> List[int]:
        """Daily units sold, excluding days where we ran out of stock.

        This matters more than it looks. Demand data is *censored* by stockouts: you sell
        zero because you had nothing to sell, not because nobody wanted it. A forecaster
        that averages in those zeros under-forecasts, under-orders, and stocks out again -
        a genuine death spiral that naive restock scripts fall into.
        """
        return [r["units_sold"] for r in self.window(sku_id, days) if not r["censored"]]

    def naive_daily_units(self, sku_id: str, days: int) -> List[int]:
        """Uncorrected version - what the manual baseline uses."""
        return [r["units_sold"] for r in self.window(sku_id, days)]

    def buy_box_rate(self, sku_id: str, days: int) -> float:
        window = self.window(sku_id, days)
        if not window:
            return 1.0
        return sum(1 for r in window if r["has_buy_box"]) / len(window)


def ewma(values: List[float], alpha: float = 0.3) -> float:
    """Exponentially weighted mean - recent days matter more than old ones."""
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def unit_margin(price: float, unit_cost: float) -> float:
    """Net profit per unit before advertising - the number that should drive ad targets."""
    return (price - unit_cost - config.referral_fee(price)
            - config.FBA_FEE_PER_UNIT - config.CLOSING_FEE_PER_UNIT)


def min_viable_price(unit_cost: float) -> float:
    """Lowest price that still clears the configured net margin floor.

    Piecewise, because amazon.in's referral fee only kicks in above Rs 1,000: solve
    the no-referral-fee case first, and only fall back to the higher-fee formula if
    that answer lands above the threshold anyway.
    """
    fixed = unit_cost + config.FBA_FEE_PER_UNIT + config.CLOSING_FEE_PER_UNIT
    below = fixed / (1 - config.MIN_NET_MARGIN_PCT)
    if below < config.ZERO_REFERRAL_FEE_BELOW:
        return round(below, 2)
    above = fixed / (1 - config.REFERRAL_FEE_RATE - config.MIN_NET_MARGIN_PCT)
    return round(max(above, config.ZERO_REFERRAL_FEE_BELOW), 2)
