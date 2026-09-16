"""
Listing / SEO layer: detect underperforming listings and trigger a rewrite.

This is the layer where full autonomy is genuinely inadvisable, and the code reflects
that. The engine's job is *diagnosis* - deciding which listing is underperforming and
why, from traffic data. The copy itself is drafted by an LLM and, in production, should
sit in a human approval queue before being pushed to Amazon. Bad listing copy does not
merely underperform: it can tank a listing that was previously fine, and recovery takes
weeks of reindexing.

Diagnosis uses only observable signals - CTR against a category benchmark (are people
clicking the title and main image?) and conversion rate (does the page deliver on the
click?). Low CTR and low CVR point at different fixes, so the engine says which.
"""

from typing import List

import config
from actions import Action
from metrics import MetricsStore


def generate_listing_copy(sku_title: str, weakness: str, top_terms: List[str]) -> dict:
    """
    INTEGRATION POINT - in production this calls an LLM.

    Real implementation would: pull the current listing via SP-API getListingsItem, pull
    converting search terms from the Advertising API search term report, prompt a model to
    draft title / bullets / description, then queue the draft for human approval before
    calling putListingsItem.

    Here it returns a deterministic stub. The part being tested by this MVP is *when and
    why* the system decides to rewrite - not the copywriting, which cannot be meaningfully
    simulated.
    """
    focus = {
        "low_ctr": "rewrite title and main image copy to lead with the primary benefit",
        "low_cvr": "rewrite bullets and A+ content to resolve the objections losing the sale",
        "both": "full rewrite: title, bullets and A+ content",
    }[weakness]
    return {
        "focus": focus,
        "draft_title": f"{sku_title} - {top_terms[0] if top_terms else 'premium quality'}",
        "requires_human_approval": True,
    }


class ListingEngine:
    layer = "listing"

    def assess(self, sku, metrics: MetricsStore, day: int) -> List[Action]:
        if day - sku.last_rewrite_day < config.LISTING_REWRITE_COOLDOWN_DAYS:
            return []

        window = metrics.window(sku.sku_id, config.LISTING_WINDOW)
        impressions = sum(r["impressions"] for r in window)
        clicks = sum(r["clicks"] for r in window)
        ad_orders = sum(sum(k["orders"] for k in r["keywords"].values()) for r in window)

        if impressions < config.LISTING_MIN_IMPRESSIONS or clicks < 30:
            return []  # too little traffic to judge the listing fairly

        ctr = clicks / impressions
        cvr = ad_orders / clicks if clicks else 0.0

        ctr_index = min(ctr / config.BENCHMARK_CTR, 2.0) / 2.0
        cvr_index = min(cvr / config.BENCHMARK_CVR, 2.0) / 2.0
        score = 0.5 * ctr_index + 0.5 * cvr_index

        if score >= config.LISTING_SCORE_FLOOR:
            return []

        if ctr_index < 0.45 and cvr_index < 0.45:
            weakness = "both"
        elif ctr_index < cvr_index:
            weakness = "low_ctr"
        else:
            weakness = "low_cvr"

        top_terms = [kw.text for kw in sku.keywords if kw.active][:1]
        draft = generate_listing_copy(sku.title, weakness, top_terms)

        sku.last_rewrite_day = day
        return [Action(
            day=day, layer=self.layer, sku_id=sku.sku_id, action_type="rewrite_listing",
            reason=(f"listing score {score:.2f} vs. {config.LISTING_SCORE_FLOOR} floor - "
                    f"CTR {ctr:.2%} vs. {config.BENCHMARK_CTR:.2%} benchmark, "
                    f"CVR {cvr:.1%} vs. {config.BENCHMARK_CVR:.0%} benchmark"),
            detail={"weakness": weakness, "focus": draft["focus"],
                    "requires_human_approval": True,
                    "reindex_days": config.LISTING_REINDEX_DAYS},
        )]
