"""
Entry point: runs the automated stack against a manual baseline on an identical market
and writes results.json for the dashboard.

Usage:  python3 run_simulation.py
"""

import json
from typing import Dict, List

import config
from orchestrator import Orchestrator


def aggregate_daily(orchestrator: Orchestrator) -> List[Dict]:
    days = []
    for day_index in range(config.SIM_DAYS):
        revenue = ad_spend = ad_sales = gross_profit = cogs = fees = 0.0
        units = lost = on_hand = 0
        stockout_skus = 0
        buy_box_held = 0
        sku_count = 0

        for sku_id, records in orchestrator.metrics.records.items():
            if day_index >= len(records):
                continue
            rec = records[day_index]
            sku_count += 1
            revenue += rec["revenue"]
            ad_spend += rec["ad_spend"]
            ad_sales += rec["ad_sales"]
            gross_profit += rec["gross_profit"]
            cogs += rec["cogs"]
            fees += rec["fees"]
            units += rec["units_sold"]
            lost += rec["lost_units"]
            on_hand += rec["on_hand_end"]
            if rec["on_hand_end"] <= 0:
                stockout_skus += 1
            if rec["has_buy_box"]:
                buy_box_held += 1

        days.append({
            "day": day_index + 1,
            "revenue": round(revenue, 2),
            "ad_spend": round(ad_spend, 2),
            "ad_sales": round(ad_sales, 2),
            "cogs": round(cogs, 2),
            "fees": round(fees, 2),
            "gross_profit": round(gross_profit, 2),
            "units_sold": units,
            "lost_units": lost,
            "on_hand": on_hand,
            "stockout_skus": stockout_skus,
            "buy_box_rate": round(buy_box_held / sku_count, 3) if sku_count else 0,
            "acos": round(ad_spend / ad_sales, 4) if ad_sales > 0 else None,
            "tacos": round(ad_spend / revenue, 4) if revenue > 0 else None,
        })
    return days


def add_rolling(days: List[Dict], window: int = 7) -> None:
    for i, day in enumerate(days):
        chunk = days[max(0, i - window + 1): i + 1]
        spend = sum(d["ad_spend"] for d in chunk)
        ad_sales = sum(d["ad_sales"] for d in chunk)
        revenue = sum(d["revenue"] for d in chunk)
        day["rolling_acos"] = round(spend / ad_sales, 4) if ad_sales > 0 else None
        day["rolling_tacos"] = round(spend / revenue, 4) if revenue > 0 else None


def per_sku_summary(orchestrator: Orchestrator) -> List[Dict]:
    summary = []
    for sku in orchestrator.skus:
        records = orchestrator.metrics.records.get(sku.sku_id, [])
        revenue = sum(r["revenue"] for r in records)
        ad_spend = sum(r["ad_spend"] for r in records)
        ad_sales = sum(r["ad_sales"] for r in records)
        summary.append({
            "sku_id": sku.sku_id,
            "title": sku.title,
            "revenue": round(revenue, 2),
            "ad_spend": round(ad_spend, 2),
            "gross_profit": round(sum(r["gross_profit"] for r in records), 2),
            "units_sold": sum(r["units_sold"] for r in records),
            "lost_units": sum(r["lost_units"] for r in records),
            "stockout_days": sum(1 for r in records if r["on_hand_end"] <= 0),
            "buy_box_days": sum(1 for r in records if r["has_buy_box"]),
            "acos": round(ad_spend / ad_sales, 4) if ad_sales > 0 else None,
            "tacos": round(ad_spend / revenue, 4) if revenue > 0 else None,
            "start_price": sku.reference_price,
            "final_price": round(sku.price, 2),
            "active_keywords": sum(1 for k in sku.keywords if k.active),
        })
    return summary


def totals_from(days: List[Dict]) -> Dict:
    revenue = sum(d["revenue"] for d in days)
    ad_spend = sum(d["ad_spend"] for d in days)
    ad_sales = sum(d["ad_sales"] for d in days)
    return {
        "revenue": round(revenue, 2),
        "ad_spend": round(ad_spend, 2),
        "ad_sales": round(ad_sales, 2),
        "cogs": round(sum(d["cogs"] for d in days), 2),
        "fees": round(sum(d["fees"] for d in days), 2),
        "gross_profit": round(sum(d["gross_profit"] for d in days), 2),
        "units_sold": sum(d["units_sold"] for d in days),
        "lost_units": sum(d["lost_units"] for d in days),
        "stockout_sku_days": sum(d["stockout_skus"] for d in days),
        "acos": round(ad_spend / ad_sales, 4) if ad_sales > 0 else None,
        "tacos": round(ad_spend / revenue, 4) if revenue > 0 else None,
        "profit_margin": round(sum(d["gross_profit"] for d in days) / revenue, 4) if revenue else None,
        "avg_buy_box_rate": round(sum(d["buy_box_rate"] for d in days) / len(days), 3) if days else None,
    }


def run_regime(mode: str) -> Dict:
    orchestrator = Orchestrator(mode)
    orchestrator.run()
    days = aggregate_daily(orchestrator)
    add_rolling(days)
    return {
        "daily": days,
        "totals": totals_from(days),
        "per_sku": per_sku_summary(orchestrator),
        "actions": [a.to_dict() for a in orchestrator.actions],
    }


def main() -> None:
    manual = run_regime("manual")
    automated = run_regime("automated")

    action_counts: Dict[str, int] = {}
    for action in automated["actions"]:
        action_counts[action["layer"]] = action_counts.get(action["layer"], 0) + 1

    action_type_counts: Dict[str, int] = {}
    for action in automated["actions"]:
        action_type_counts[action["action_type"]] = action_type_counts.get(action["action_type"], 0) + 1

    output = {
        "config": {
            "days": config.SIM_DAYS,
            "skus": len(automated["per_sku"]),
            "keywords_per_sku": config.KEYWORDS_PER_SKU,
            "target_margin_share": config.TARGET_MARGIN_SHARE,
            "max_bid_change_pct": config.MAX_BID_CHANGE_PCT,
            "max_price_change_pct": config.MAX_PRICE_CHANGE_PCT,
            "min_net_margin_pct": config.MIN_NET_MARGIN_PCT,
            "target_days_cover": config.TARGET_DAYS_COVER,
        },
        "manual": manual,
        "automated": automated,
        "action_counts": action_counts,
        "action_type_counts": action_type_counts,
    }

    with open("results.json", "w") as handle:
        json.dump(output, handle, indent=2)

    m, a = manual["totals"], automated["totals"]
    print(f"{'':22}{'MANUAL':>14}{'AUTOMATED':>14}")
    for label, key, fmt in [
        ("Revenue", "revenue", "${:,.0f}"),
        ("Ad spend", "ad_spend", "${:,.0f}"),
        ("Gross profit", "gross_profit", "${:,.0f}"),
        ("Profit margin", "profit_margin", "{:.1%}"),
        ("ACOS", "acos", "{:.1%}"),
        ("TACoS", "tacos", "{:.1%}"),
        ("Units sold", "units_sold", "{:,.0f}"),
        ("Lost units", "lost_units", "{:,.0f}"),
        ("Stockout SKU-days", "stockout_sku_days", "{:,.0f}"),
        ("Buy box rate", "avg_buy_box_rate", "{:.1%}"),
    ]:
        mv = fmt.format(m[key]) if m[key] is not None else "-"
        av = fmt.format(a[key]) if a[key] is not None else "-"
        print(f"{label:22}{mv:>14}{av:>14}")
    print("\nActions by layer:", action_counts)
    print("Actions by type:", action_type_counts)
    print("\nWrote results.json")


if __name__ == "__main__":
    main()
