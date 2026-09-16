"""
Advertising layer: per-keyword bid management, waste elimination, and harvesting.

The important design choice here is that the ACOS target is *derived from unit margin*
rather than fixed. A flat "25% ACOS" target - the default in most off-the-shelf tools -
is profitable on a 40%-margin product and quietly ruinous on a 17%-margin one. Deriving
the target per SKU means the engine automatically advertises thin-margin products
conservatively and fat-margin products aggressively, without anyone configuring it.
"""

import math
from dataclasses import dataclass
from typing import List

import config
from actions import Action
from metrics import MetricsStore, unit_margin


@dataclass
class PPCConstraint:
    """Handed down by the orchestrator, based on inventory and buy box state."""
    bid_multiplier: float = 1.0
    allow_increase: bool = True
    note: str = "normal operation"


def bounded_bid(old_bid: float, factor: float) -> float:
    """Apply a bid change, rounding *toward* the old bid.

    Naive `round(old * factor, 2)` can push a move past its own cap: $0.17 x 1.15 is
    $0.1955, which rounds up to $0.20 - a 17.6% move under a 15% guardrail. On cent-scale
    bids that error is material. Rounding down on increases and up on decreases means the
    guardrail holds after rounding, not just before it.
    """
    raw = old_bid * factor
    stepped = math.floor(raw * 100) / 100 if factor > 1 else math.ceil(raw * 100) / 100
    return round(min(config.MAX_BID, max(config.MIN_BID, stepped)), 2)


class PPCEngine:
    layer = "ppc"

    def __init__(self):
        # Keywords where a bid increase failed to pay for itself. Raising a bid buys
        # more clicks, but the extra clicks are the ones further down the intent curve -
        # so a keyword can sit comfortably under its ACOS target on average while the
        # marginal click loses money. Average ACOS cannot see that; only comparing
        # profit before and after an increase can.
        self._raise_watch = {}   # keyword_id -> {"day": int, "contribution": float}
        self._raise_exhausted = set()

    def target_acos(self, sku) -> float:
        """Break-even ACOS is margin/price; we target a fraction of it so ads stay profitable."""
        margin = unit_margin(sku.price, sku.unit_cost)
        if margin <= 0:
            return 0.02
        break_even = margin / sku.price
        return max(0.03, min(0.60, config.TARGET_MARGIN_SHARE * break_even))

    def decide(self, sku, metrics: MetricsStore, constraint: PPCConstraint, day: int) -> List[Action]:
        actions: List[Action] = []
        target = self.target_acos(sku)
        margin = unit_margin(sku.price, sku.unit_cost)

        for kw in sku.keywords:
            if not kw.active:
                continue

            totals = metrics.keyword_window(sku.sku_id, kw.keyword_id, config.PPC_ROLLING_WINDOW)
            clicks, spend, orders, sales = (
                totals["clicks"], totals["spend"], totals["orders"], totals["sales"])

            # Rule 1 - kill proven waste. Threshold is anchored to unit profit: once a
            # keyword has burned more than 1.5 units' worth of margin with zero orders on a
            # meaningful click sample, it is not a bidding problem, it is a bad keyword.
            waste_threshold = max(config.MIN_WASTE_THRESHOLD, margin * 1.5)
            if clicks >= config.MIN_CLICKS_TO_NEGATE and orders == 0 and spend > waste_threshold:
                kw.active = False
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="negate_keyword",
                    reason=(f"{clicks} clicks and ${spend:.2f} spent with zero orders in "
                            f"{config.PPC_ROLLING_WINDOW}d - more than 1.5 units of margin wasted"),
                    target=kw.keyword_id, before=kw.bid, after=0.0,
                ))
                continue

            if clicks < config.MIN_CLICKS_TO_ACT:
                continue  # not enough signal - acting now would be reacting to noise

            old_bid = kw.bid

            if sales <= 0:
                new_bid = bounded_bid(old_bid, 1 - config.MAX_BID_CHANGE_PCT)
                if new_bid != old_bid:
                    kw.bid = new_bid
                    actions.append(Action(
                        day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_down",
                        reason=f"{clicks} clicks, no conversions yet - trimming exposure",
                        target=kw.keyword_id, before=old_bid, after=new_bid,
                    ))
                continue

            acos = spend / sales

            # Did the last bid increase on this keyword actually earn its keep?
            contribution = orders * margin - spend
            watch = self._raise_watch.get(kw.keyword_id)
            if watch is not None and day - watch["day"] >= config.PPC_ROLLING_WINDOW:
                self._raise_watch.pop(kw.keyword_id, None)
                if contribution <= watch["contribution"]:
                    self._raise_exhausted.add(kw.keyword_id)
                    reverted = bounded_bid(kw.bid, 1 - config.MAX_BID_RAISE_PCT)
                    actions.append(Action(
                        day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_down",
                        reason=(f"last increase did not pay for itself - profit contribution "
                                f"{watch['contribution']:.0f} -> {contribution:.0f} over "
                                f"{config.PPC_ROLLING_WINDOW}d; reverting and holding"),
                        target=kw.keyword_id, before=kw.bid, after=reverted,
                    ))
                    kw.bid = reverted
                    continue

            if acos > target * config.ACOS_HIGH_TOLERANCE:
                overshoot = min(acos / target - 1, 1.0)
                cut = min(config.MAX_BID_CHANGE_PCT, 0.05 + overshoot * 0.10)
                new_bid = bounded_bid(old_bid, 1 - cut)
                if new_bid != old_bid:
                    kw.bid = new_bid
                    actions.append(Action(
                        day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_down",
                        reason=f"ACOS {acos:.0%} vs. {target:.0%} target (target set by {margin:.2f} unit margin)",
                        target=kw.keyword_id, before=old_bid, after=new_bid,
                    ))
            elif (acos < target * config.ACOS_LOW_TOLERANCE and constraint.allow_increase
                  and kw.keyword_id not in self._raise_exhausted
                  and kw.keyword_id not in self._raise_watch):
                # Raise slowly (8%) but cut fast (up to 15%). Asymmetry is deliberate:
                # overspending costs money every day it persists, whereas underbidding
                # only costs opportunity, so the downside risk deserves the faster lever.
                new_bid = bounded_bid(old_bid, 1 + config.MAX_BID_RAISE_PCT)
                if new_bid != old_bid:
                    kw.bid = new_bid
                    self._raise_watch[kw.keyword_id] = {"day": day, "contribution": contribution}
                    actions.append(Action(
                        day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_up",
                        reason=f"ACOS {acos:.0%} well under {target:.0%} target - testing more volume",
                        target=kw.keyword_id, before=old_bid, after=new_bid,
                    ))

            # Rule 2 - harvest. A broad keyword converting consistently means there is a
            # specific search term worth its own exact-match target and its own bid.
            if kw.match_type == "broad" and orders >= config.HARVEST_MIN_ORDERS and not kw.harvested:
                kw.harvested = True
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="harvest",
                    reason=f"{orders} orders in {config.PPC_ROLLING_WINDOW}d on broad match - promoting winning term to exact",
                    target=kw.keyword_id,
                ))
        return actions


class ManualPPCPolicy:
    """Baseline: the monthly PPC cleanup an organised seller actually does.

    Pulls a 30-day report, kills obviously dead keywords, cuts bids on the worst
    offenders and raises them on the clear winners. Same instincts as the engine above -
    just monthly instead of daily, with a flat ACOS rule of thumb instead of a
    margin-derived target, and no coordination with stock position.
    """
    layer = "ppc"
    REVIEW_EVERY_DAYS = 30
    RULE_OF_THUMB_ACOS = 0.30

    def decide(self, sku, metrics: MetricsStore, constraint, day: int) -> List[Action]:
        if day % self.REVIEW_EVERY_DAYS != 0:
            return []

        actions: List[Action] = []
        for kw in sku.keywords:
            if not kw.active:
                continue
            totals = metrics.keyword_window(sku.sku_id, kw.keyword_id, self.REVIEW_EVERY_DAYS)
            clicks, spend, orders, sales = (
                totals["clicks"], totals["spend"], totals["orders"], totals["sales"])

            if clicks >= 40 and orders == 0:
                kw.active = False
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="negate_keyword",
                    reason=f"monthly review: {clicks} clicks, no orders",
                    target=kw.keyword_id, before=kw.bid, after=0.0,
                ))
                continue

            if sales <= 0:
                continue
            acos = spend / sales
            old_bid = kw.bid
            if acos > self.RULE_OF_THUMB_ACOS * 1.5:
                kw.bid = round(max(config.MIN_BID, old_bid * 0.8), 2)
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_down",
                    reason=f"monthly review: ACOS {acos:.0%} too high",
                    target=kw.keyword_id, before=old_bid, after=kw.bid,
                ))
            elif acos < self.RULE_OF_THUMB_ACOS * 0.5:
                kw.bid = round(min(config.MAX_BID, old_bid * 1.2), 2)
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="bid_up",
                    reason=f"monthly review: ACOS {acos:.0%}, scaling up",
                    target=kw.keyword_id, before=old_bid, after=kw.bid,
                ))
        return actions
