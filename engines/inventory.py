"""
Inventory layer: demand forecasting, restock planning, and stock-position classification.

Its second job is arguably more important than its first: it publishes a *stock state*
that the orchestrator uses to constrain the advertising and pricing layers. Spending
aggressively on ads for a SKU with nine days of cover and a 45-day lead time is one of
the most expensive mistakes an Amazon seller can make - you pay to accelerate your own
stockout, then lose the organic rank you bought.
"""

from dataclasses import dataclass
from typing import List, Tuple

import config
from actions import Action
from metrics import MetricsStore, ewma


@dataclass
class StockSignal:
    state: str            # critical | low | healthy | overstock
    forecast_units: float # forecast daily demand
    cover_on_hand: float  # days of cover from stock physically in the warehouse
    cover_pipeline: float # days of cover including inbound shipments


class InventoryEngine:
    layer = "inventory"

    def assess(self, sku, metrics: MetricsStore, day: int) -> Tuple[StockSignal, List[Action]]:
        # Forecast from clean (non-stockout) days only - see metrics.clean_daily_units
        clean = metrics.clean_daily_units(sku.sku_id, config.FORECAST_WINDOW)
        if clean:
            forecast = max(0.5, ewma(clean))
        else:
            # Everything in the window was censored: fall back to the last clean signal we
            # have rather than forecasting from stockout zeros.
            fallback = metrics.clean_daily_units(sku.sku_id, 60)
            forecast = max(0.5, ewma(fallback)) if fallback else 1.0

        inbound_units = sum(units for _, units in sku.inbound)
        cover_on_hand = sku.on_hand / forecast
        cover_pipeline = (sku.on_hand + inbound_units) / forecast

        if cover_on_hand < config.CRITICAL_STOCK_DAYS:
            state = "critical"
        elif cover_on_hand < config.LOW_STOCK_DAYS:
            state = "low"
        elif cover_on_hand > config.OVERSTOCK_DAYS:
            state = "overstock"
        else:
            state = "healthy"

        signal = StockSignal(state, round(forecast, 2), round(cover_on_hand, 1), round(cover_pipeline, 1))

        actions: List[Action] = []
        reorder_point_days = sku.lead_time_days + config.SAFETY_DAYS
        if cover_pipeline < reorder_point_days:
            target_units = forecast * (config.TARGET_DAYS_COVER + sku.lead_time_days)
            qty = int(min(config.MAX_ORDER_UNITS, max(0, target_units - sku.on_hand - inbound_units)))
            if qty >= config.MIN_ORDER_UNITS:
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="restock",
                    reason=(f"{cover_pipeline:.0f} days cover incl. inbound vs. {reorder_point_days}-day "
                            f"reorder point ({sku.lead_time_days}d lead time + {config.SAFETY_DAYS}d safety)"),
                    before=float(sku.on_hand), after=float(sku.on_hand + qty),
                    detail={"units": qty, "forecast_daily": round(forecast, 2),
                            "arrives_day": day + sku.lead_time_days},
                ))
        return signal, actions


class ManualInventoryPolicy:
    """The baseline: a seller eyeballing stock every couple of weeks.

    This seller is not careless - they know roughly what their lead time is and reorder
    with that in mind. What they lack is (a) safety stock for demand variability and
    (b) any correction for censored demand: they forecast from a flat average of recent
    sales, stockout days and all, which quietly drags the forecast down after every
    stockout and makes the next one more likely.
    """
    layer = "inventory"

    def assess(self, sku, metrics: MetricsStore, day: int) -> Tuple[StockSignal, List[Action]]:
        naive = metrics.naive_daily_units(sku.sku_id, config.MANUAL_FORECAST_WINDOW)
        forecast = max(0.5, sum(naive) / len(naive)) if naive else 1.0
        inbound_units = sum(units for _, units in sku.inbound)
        cover_on_hand = sku.on_hand / forecast
        cover_pipeline = (sku.on_hand + inbound_units) / forecast
        signal = StockSignal("healthy", round(forecast, 2), round(cover_on_hand, 1), round(cover_pipeline, 1))

        actions: List[Action] = []
        reorder_at = sku.lead_time_days * config.MANUAL_REORDER_LEAD_TIME_SHARE
        if cover_pipeline < reorder_at:
            qty = int(min(config.MAX_ORDER_UNITS, forecast * config.MANUAL_ORDER_COVER_DAYS))
            if qty >= config.MIN_ORDER_UNITS:
                actions.append(Action(
                    day=day, layer=self.layer, sku_id=sku.sku_id, action_type="restock",
                    reason="manual reorder: stock looked low",
                    detail={"units": qty, "arrives_day": day + sku.lead_time_days},
                ))
        return signal, actions
