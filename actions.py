"""Action objects.

Engines never mutate the world directly - they return Actions, and the orchestrator
applies them. That separation is what makes 'shadow mode' possible: run the exact same
engines against a real account, collect the Actions, and review them instead of
executing them. It is the single most important design decision in this MVP, because
it is what lets you build trust before the system touches real money.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Action:
    day: int
    layer: str          # ppc | pricing | inventory | listing | coordination
    sku_id: str
    action_type: str    # bid_up, bid_down, negate_keyword, harvest, price_change, restock, rewrite_listing, throttle_ads
    reason: str         # human-readable justification - what a client would see in a report
    target: Optional[str] = None     # keyword id, where relevant
    before: Optional[float] = None
    after: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "layer": self.layer,
            "sku_id": self.sku_id,
            "action_type": self.action_type,
            "reason": self.reason,
            "target": self.target,
            "before": self.before,
            "after": self.after,
            "detail": self.detail,
        }
