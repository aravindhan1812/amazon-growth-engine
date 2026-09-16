# Amazon Growth Engine — MVP

A working model of the four operational layers an Amazon growth service automates:
advertising, pricing, inventory, and listing quality — plus the coordination layer that
makes them a system rather than four disconnected tools.

**Live dashboard: https://aravindhan1812.github.io/amazon-growth-engine/**

Run it:

```bash
python3 run_simulation.py    # runs the 90-day A/B, writes results.json
python3 build_dashboard.py   # renders docs/index.html
```

No dependencies beyond the Python standard library. Open `docs/index.html` in any
browser — it is fully self-contained, so it also works as an email attachment.

## Publishing the dashboard

`docs/index.html` is committed (unlike `results.json`, which is generated) because
GitHub Pages can only serve files that are in the repository.

```bash
git remote add origin https://github.com/aravindhan1812/amazon-growth-engine.git
git push -u origin main
```

Then in the repo: **Settings → Pages → Source: Deploy from a branch → Branch: `main`,
folder: `/docs` → Save.** The dashboard appears at
https://aravindhan1812.github.io/amazon-growth-engine/ within a minute or two.

Whenever you rerun the simulation, rebuild and push to update the published page:

```bash
python3 run_simulation.py && python3 build_dashboard.py
git commit -am "Refresh dashboard" && git push
```

A public Pages site is fine for this repo because the data is simulated. Once the
engines are reading a real seller's numbers, do not publish the dashboard — that is
confidential business data. Keep the repo private and share the HTML file directly.

## Results (90-day simulation)

|                    | Manual baseline | Automated | Δ         |
|--------------------|-----------------|-----------|-----------|
| Revenue            | $300,207        | $322,800  | +7.5%     |
| Ad spend           | $11,168         | $11,254   | +0.8%     |
| **Gross profit**   | **$81,916**     | **$94,186** | **+15.0%** |
| Profit margin      | 27.3%           | 29.2%     | +1.9 pts  |
| Stockout SKU-days  | 69              | 23        | −46       |
| Buy box rate       | 83.3%           | 85.4%     | +2.1 pts  |

The profit gain is not bought with ad budget — spend is essentially flat. It comes from
not stocking out, not losing the buy box, and not over-advertising thin-margin products.

Note that automated ACOS is slightly *worse* (20.3% vs 19.8%). That is deliberate: the
engine targets profit, and will accept a higher ACOS on a high-margin SKU where the
economics support it. Optimising ACOS directly is a common and expensive mistake.

## Architecture

```
world.py          the simulated marketplace — hidden truth (demand, elasticity, intent)
      │
      ▼ produces only observable metrics
metrics.py        the observability boundary — mirrors what Amazon's APIs actually expose
      │
      ▼ read by
engines/
  ppc.py          per-keyword bidding, waste elimination, harvesting
  pricing.py      buy-box-aware repricing inside a hard margin floor
  inventory.py    demand forecasting, restock planning, stock-state classification
  listing.py      listing diagnosis from CTR/CVR, rewrite trigger (LLM integration point)
      │
      ▼ return Action objects — never mutate anything
orchestrator.py   daily cycle + the cross-layer rules
```

### Three design decisions that matter

**1. Engines return Actions; they never execute.** The orchestrator applies them. This is
what makes *shadow mode* possible: point the same engines at a real account, collect the
Actions, and review them instead of executing. You can prove the system's judgment on a
live account for weeks before it is allowed to touch a bid.

**2. The observability boundary is enforced.** Engines read `metrics.py` only, never
`world.py`. They cannot see true demand, price elasticity, keyword intent, or listing
quality — because a real seller cannot either. `verify.py`-style checks in the build
confirm no engine imports the world model or reads hidden fields. Without this discipline
a simulation flatters itself and teaches you nothing.

**3. ACOS targets are derived from unit margin, not configured.** Break-even ACOS is
`unit_margin / price`; the engine targets 55% of it. On SKU-A (41% margin) that is a ~23%
target; on SKU-D (17% margin) it is ~9%. A single flat "25% ACOS" target — the default in
most off-the-shelf tools — would quietly lose money on SKU-D every single day.

### The cross-layer rules

This is the part you cannot buy as a point solution:

| Condition | Effect on advertising | Why |
|---|---|---|
| Out of stock | Ads ineligible | Amazon stops serving them anyway |
| Buy box lost | **Ads paused entirely** | Paid clicks convert ~8x worse without it — near-pure waste |
| < 7 days cover | Bids throttled to 25%, increases blocked | Don't pay to accelerate a stockout you can't restock for weeks |
| < 14 days cover | Bids throttled to 60% | Reduce burn ahead of restock |
| Overstocked | Bids raised 25%, price cut | Clear inventory accruing storage fees |

Pricing reads stock position too: when cover is tight it *raises* price to slow the burn
and harvest margin, which no competitor-matching repricer will ever do.

## What is real and what is not

**Real:** all the decision logic, the guardrails, the economics (Amazon's referral fee,
FBA fee, storage fee, true unit margin), the censoring-aware forecasting, and the
architecture.

**Simulated:** the marketplace. Demand curves, competitor behaviour and conversion
physics are synthetic. They are directionally sensible but not calibrated to any real
account, so **the numbers above demonstrate that the logic works — they are not a
forecast of what this would earn.**

**Absent:** dayparting, placement-level bid modifiers, Sponsored Brands/Display, multi-
marketplace, variations/parentage, promotions and coupons, competitor listing analysis,
returns and reimbursements, and the strategic judgment layer (which products to push,
how to react to a competitor's launch, what to do with a product that has no history).

## Going from this to a real system

Swap `world.py` for real API calls. Nothing in `engines/` should need to change — that is
the test of whether the boundary was drawn correctly.

| What the simulator provides | Real source |
|---|---|
| keyword impressions/clicks/spend/orders | Advertising API — Sponsored Products reports (v3) |
| bid and keyword updates | Advertising API — `PUT /sp/keywords`, campaign budget endpoints |
| units sold, revenue | SP-API — Orders API, Business Reports |
| price, competitor price, buy box owner | SP-API — Product Pricing (`getItemOffers`, `IsBuyBoxWinner`) |
| on-hand and inbound inventory | SP-API — FBA Inventory (`getInventorySummaries`) |
| listing content updates | SP-API — Listings Items (`putListingsItem`) |
| listing copy drafting | LLM call — see `generate_listing_copy()` in `engines/listing.py` |

Getting Advertising API access requires an approved developer account tied to a real
seller or agency account, and approval takes time — start that before anything else.

### Suggested path

1. **Read-only.** Connect the APIs, populate `metrics.py` from real data, change nothing.
2. **Shadow mode.** Run the engines daily, log Actions, execute none. Compare the log
   against what actually happened for 3–4 weeks. This is where you find out whether the
   logic survives real noise, seasonality and Amazon's actual auction dynamics.
3. **Supervised execution.** Human approves each Action before it fires. Start with the
   lowest-risk layer (negative keywords), not pricing.
4. **Autonomous within bounds.** Let PPC and inventory run unattended inside the
   guardrails. Keep pricing and listing changes human-approved for much longer — those
   are the two that can do lasting damage.

Before any of this touches a real account you also need: monitoring and alerting, an
audit log, a kill switch, rollback for every action type, rate-limit and retry handling,
and tests for the edge cases the simulator never produces (suppressed listings, account
health issues, Amazon API outages, campaigns paused outside the system).
