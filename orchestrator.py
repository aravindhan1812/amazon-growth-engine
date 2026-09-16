"""
The orchestrator: runs the daily decision cycle and coordinates the layers.

This file is the actual product. Anyone can buy a repricer, a bid tool and a forecasting
tool separately - but those tools do not talk to each other, and that is where sellers
lose money. The cross-layer rules in `_derive_ppc_constraint` are the difference:

  * out of stock        -> ads are ineligible anyway; stop counting on them
  * buy box lost        -> pause ads entirely. Paid traffic converts roughly 8x worse
                           without the buy box, so every click is close to pure waste.
                           This single rule is usually the biggest profit leak in an
                           un-coordinated setup.
  * < 7 days cover      -> throttle bids hard and forbid increases. Do not pay to
                           accelerate a stockout you cannot restock for weeks.
  * < 14 days cover     -> throttle moderately.
  * overstocked         -> lean in. Ads and a price cut together clear inventory that is
                           otherwise accruing storage fees.

Each decision is logged with a plain-English reason, because the output that matters to a
client is not the bid change - it is being able to explain why it happened.
"""

from typing import Dict, List

import config
import world
from actions import Action
from engines.inventory import InventoryEngine, ManualInventoryPolicy, StockSignal
from engines.listing import ListingEngine
from engines.ppc import ManualPPCPolicy, PPCConstraint, PPCEngine
from engines.pricing import ManualPricingPolicy, PricingEngine
from metrics import MetricsStore


class Orchestrator:
    def __init__(self, mode: str):
        assert mode in ("automated", "manual")
        self.mode = mode
        self.metrics = MetricsStore()
        self.actions: List[Action] = []
        self.skus = world.build_catalog()
        self._last_constraint: Dict[str, str] = {}

        if mode == "automated":
            self.inventory = InventoryEngine()
            self.pricing = PricingEngine()
            self.ppc = PPCEngine()
            self.listing = ListingEngine()
        else:
            # The baseline is a competent seller working manually: fortnightly buy box
            # checks, a monthly PPC cleanup, and eyeballed restocking. What it lacks is
            # daily cadence and any coordination between the layers.
            self.inventory = ManualInventoryPolicy()
            self.pricing = ManualPricingPolicy()
            self.ppc = ManualPPCPolicy()

    # ------------------------------------------------------------------ cross-layer
    def _derive_ppc_constraint(self, sku, signal: StockSignal) -> PPCConstraint:
        if sku.on_hand <= 0:
            return PPCConstraint(0.0, False, "out of stock - ads ineligible")
        if not sku.has_buy_box:
            return PPCConstraint(0.0, False,
                                 "buy box lost - ads paused (paid clicks convert ~8x worse without it)")
        if signal.state == "critical":
            return PPCConstraint(0.25, False,
                                 f"{signal.cover_on_hand:.0f} days cover - throttling ads to avoid "
                                 f"paying to accelerate a stockout")
        if signal.state == "low":
            return PPCConstraint(0.60, False, f"{signal.cover_on_hand:.0f} days cover - reducing ad pressure")
        if signal.state == "overstock":
            return PPCConstraint(1.25, True,
                                 f"{signal.cover_on_hand:.0f} days cover - increasing ad pressure to clear stock")
        return PPCConstraint(1.0, True, "normal operation")

    def _apply_action(self, action: Action, sku) -> None:
        if action.action_type == "restock":
            sku.inbound.append((action.detail["arrives_day"], action.detail["units"]))
        elif action.action_type == "rewrite_listing":
            sku.pending_listing_boost = (action.day + config.LISTING_REINDEX_DAYS,
                                         config.LISTING_QUALITY_LIFT)
        # bid, negation and price changes are applied by their engines directly on the
        # objects they own; restock and listing changes need the world's cooperation.

    # ------------------------------------------------------------------ daily cycle
    def run_day(self, day: int) -> None:
        # 1. The market happens
        for index, sku in enumerate(self.skus):
            record = world.simulate_day(sku, index, day)
            self.metrics.record(record)

        # 2. We observe it and decide what to do tomorrow
        for sku in self.skus:
            signal, inventory_actions = self.inventory.assess(sku, self.metrics, day)
            for action in inventory_actions:
                self._apply_action(action, sku)
            self.actions.extend(inventory_actions)

            if self.mode != "automated":
                # Manual mode still reprices and reviews ads - just on a human cadence,
                # and with no cross-layer coordination (bid_multiplier stays at 1.0).
                self.actions.extend(self.pricing.decide(sku, self.metrics, signal, day))
                self.actions.extend(self.ppc.decide(sku, self.metrics, PPCConstraint(), day))
                continue

            price_actions = self.pricing.decide(sku, self.metrics, signal, day)
            self.actions.extend(price_actions)

            constraint = self._derive_ppc_constraint(sku, signal)
            if self._last_constraint.get(sku.sku_id) != constraint.note:
                self._last_constraint[sku.sku_id] = constraint.note
                if constraint.bid_multiplier != 1.0:
                    self.actions.append(Action(
                        day=day, layer="coordination", sku_id=sku.sku_id,
                        action_type="throttle_ads", reason=constraint.note,
                        before=sku.bid_multiplier, after=constraint.bid_multiplier,
                    ))
            sku.bid_multiplier = constraint.bid_multiplier

            self.actions.extend(self.ppc.decide(sku, self.metrics, constraint, day))

            listing_actions = self.listing.assess(sku, self.metrics, day)
            for action in listing_actions:
                self._apply_action(action, sku)
            self.actions.extend(listing_actions)

    def run(self) -> None:
        for day in range(1, config.SIM_DAYS + 1):
            self.run_day(day)
