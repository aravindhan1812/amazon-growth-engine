"""
Pricing layer: buy-box-aware repricing bounded by a hard margin floor.

Two things make this different from a naive repricer that just undercuts:
  1. It never prices below the configured net margin floor, no matter what the
     competitor does. Winning the buy box at a loss is not winning.
  2. It uses stock position as an input. When cover is tight, raising price is the
     correct move - it slows the burn rate and harvests margin from the demand you
     can still serve. Naive repricers only ever look sideways at competitors.
"""

from typing import List

import config
from actions import Action
from metrics import MetricsStore, min_viable_price


class PricingEngine:
    layer = "pricing"

    def decide(self, sku, metrics: MetricsStore, signal, day: int) -> List[Action]:
        if day - sku.last_price_change_day < config.PRICE_CHANGE_COOLDOWN_DAYS:
            return []

        floor = min_viable_price(sku.unit_cost)
        buy_box_rate = metrics.buy_box_rate(sku.sku_id, 5)
        target = sku.price
        reason = None

        if buy_box_rate < 0.6 and sku.competitor_price < sku.price:
            # Losing the buy box to a cheaper competitor is the most expensive state to be
            # in: traffic still arrives, ads still spend, almost nothing converts.
            target = sku.competitor_price * 0.995
            reason = (f"buy box held only {buy_box_rate:.0%} of last 5 days and competitor is at "
                      f"${sku.competitor_price:.2f} - repricing to recover it")
        elif signal.state in ("critical", "low") and buy_box_rate > 0.8:
            target = sku.price * 1.04
            reason = (f"{signal.cover_on_hand:.0f} days cover with {sku.lead_time_days}d lead time - "
                      f"raising price to slow burn rate and protect margin until restock lands")
        elif signal.state == "overstock":
            target = sku.price * 0.96
            reason = f"{signal.cover_on_hand:.0f} days cover - discounting to clear excess inventory"
        elif buy_box_rate > 0.9 and sku.price < sku.competitor_price * 0.95 and signal.state == "healthy":
            target = min(sku.competitor_price * 0.98, sku.price * 1.03)
            reason = (f"holding buy box at {sku.price:.2f} while competitor sits at "
                      f"${sku.competitor_price:.2f} - recovering margin left on the table")

        if reason is None:
            return []

        # Guardrails: bounded move, hard margin floor
        upper = sku.price * (1 + config.MAX_PRICE_CHANGE_PCT)
        lower = sku.price * (1 - config.MAX_PRICE_CHANGE_PCT)
        new_price = round(max(floor, min(upper, max(lower, target))), 2)

        if abs(new_price - sku.price) < 0.05:
            return []

        old_price = sku.price
        sku.price = new_price
        sku.last_price_change_day = day
        return [Action(
            day=day, layer=self.layer, sku_id=sku.sku_id, action_type="price_change",
            reason=reason, before=old_price, after=new_price,
            detail={"competitor_price": sku.competitor_price, "margin_floor": floor},
        )]


class ManualPricingPolicy:
    """Baseline: a seller who checks the buy box roughly every fortnight.

    When they notice they have lost it, they match the competitor and move on. No
    stock-awareness, no margin recovery when they are under-priced - but crucially, not
    asleep either. The automated engine has to beat this, not a seller who never looks.
    """
    layer = "pricing"
    REVIEW_EVERY_DAYS = 14

    def decide(self, sku, metrics: MetricsStore, signal, day: int) -> List[Action]:
        if day % self.REVIEW_EVERY_DAYS != 0:
            return []
        buy_box_rate = metrics.buy_box_rate(sku.sku_id, 7)
        if buy_box_rate >= 0.5 or sku.competitor_price >= sku.price:
            return []

        floor = min_viable_price(sku.unit_cost)
        new_price = round(max(floor, sku.competitor_price - 0.01), 2)
        if abs(new_price - sku.price) < 0.05:
            return []

        old_price = sku.price
        sku.price = new_price
        sku.last_price_change_day = day
        return [Action(
            day=day, layer=self.layer, sku_id=sku.sku_id, action_type="price_change",
            reason="fortnightly manual check: buy box lost, matched competitor",
            before=old_price, after=new_price,
        )]
