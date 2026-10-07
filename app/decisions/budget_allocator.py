"""Constraint-aware budget allocation across campaigns.

Weights = opportunity score x sqrt(contribution ROAS) (sqrt = diminishing
returns, so one huge-ROAS campaign cannot swallow the whole budget).
Hard constraints: out-of-stock / fatigued / loss-making campaigns get nothing,
no campaign exceeds `max_pct`, and tiny slices below `min_pct` are dropped.
"""
from __future__ import annotations

import math

from app import config


def _eligible(o: dict) -> bool:
    return (o.get("stock_headroom", 0) > 0
            and o.get("oos_exposure", 0.0) < 0.5
            and o.get("creative_fatigue", 0.0) < 0.9
            and o.get("contribution_roas", o.get("roas", 0)) >= 1.0)


def _waterfill(weights: dict[str, float], cap: float) -> dict[str, float]:
    """Shares proportional to weights, none above `cap`, summing to 1."""
    n = len(weights)
    cap = max(cap, 1.0 / n)            # infeasible cap -> equalise
    shares: dict[str, float] = {}
    free = dict(weights)
    remaining = 1.0
    while free:
        tw = sum(free.values())
        if tw <= 0:
            for k in free:
                shares[k] = remaining / len(free)
            break
        over = {k: w for k, w in free.items() if remaining * w / tw > cap + 1e-12}
        if not over:
            for k, w in free.items():
                shares[k] = remaining * w / tw
            break
        for k in over:
            shares[k] = cap
            remaining -= cap
            del free[k]
    return shares


def allocate_detail(opportunities: list[dict], total_budget: float = 10000.0,
                    min_pct: float = config.ALLOC_MIN_PCT,
                    max_pct: float = config.ALLOC_MAX_PCT) -> list[dict]:
    pool = [o for o in opportunities if _eligible(o)]
    if not pool or total_budget <= 0:
        return []

    cur_total = sum(o.get("daily_spend", 0) for o in opportunities) or 1.0
    weights = {o["campaign_id"]: o["opportunity_score"]
               * math.sqrt(max(o.get("contribution_roas", o["roas"]), 0.1))
               for o in pool}
    shares = _waterfill(weights, max_pct)
    for _ in range(5):                       # drop slivers, re-balance
        small = [k for k, s in shares.items() if s < min_pct]
        if not small or len(small) == len(shares):
            break
        weights = {k: w for k, w in weights.items() if k not in small}
        shares = _waterfill(weights, max_pct)

    by_id = {o["campaign_id"]: o for o in pool}
    rows = []
    for cid, s in sorted(shares.items(), key=lambda kv: -kv[1]):
        o = by_id[cid]
        cur = o.get("daily_spend", 0) / cur_total
        rows.append({
            "campaign_id": cid, "platform": o["platform"],
            "budget": round(total_budget * s, 2),
            "share_pct": round(s * 100, 2),
            "current_share_pct": round(cur * 100, 2),
            "shift_pct_points": round((s - cur) * 100, 2),
            "opportunity_score": o["opportunity_score"],
        })
    # make the rounded budgets add up exactly
    diff = round(total_budget - sum(r["budget"] for r in rows), 2)
    if rows and diff:
        rows[0]["budget"] = round(rows[0]["budget"] + diff, 2)
    return rows


def allocate(opportunities: list[dict], total_budget: float = 10000.0,
             min_pct: float = config.ALLOC_MIN_PCT,
             max_pct: float = config.ALLOC_MAX_PCT) -> dict[str, float]:
    return {r["campaign_id"]: r["budget"]
            for r in allocate_detail(opportunities, total_budget, min_pct, max_pct)}
