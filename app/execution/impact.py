"""Measure before/after impact of an executed action.

The effective date is `params.effective_date` if given, otherwise the
execution timestamp. If no post-action data exists yet (e.g. the dataset ends
before the action), the result is `pending_data` with the baseline and a
clearly labelled projection - never a silent block of zeros.
"""
from __future__ import annotations

import pandas as pd

from app.data.loader import load_unified

MARGINAL_FACTOR = 0.7


def _target_filter(ads: pd.DataFrame, action: str, target: str) -> pd.DataFrame:
    if action in {"SCALE_BUDGET", "CUT_BUDGET", "REDUCE_SPEND", "PAUSE_CAMPAIGN",
                  "INVESTIGATE_ATTRIBUTION"}:
        return ads[ads["campaign_id"] == target]
    if action == "HOLD_PLATFORM_SPEND":
        return ads[ads["platform"] == target]
    if action == "REFRESH_CREATIVE":
        return ads[ads["creative_id"] == target]
    if action == "INVESTIGATE_SITE":
        return ads
    return ads.iloc[0:0]


def _agg(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"days": 0, "spend": 0.0, "revenue": 0.0, "roas": 0.0,
                "clicks": 0, "units": 0, "cvr": 0.0}
    spend, rev = float(df["spend"].sum()), float(df["revenue"].sum())
    clicks, units = int(df["clicks"].sum()), int(df["true_units"].sum())
    return {"days": int(df["date"].nunique()), "spend": round(spend, 2),
            "revenue": round(rev, 2),
            "roas": round(rev / spend, 2) if spend else 0.0,
            "clicks": clicks, "units": units,
            "cvr": round(units / clicks, 4) if clicks else 0.0}


def _pct(new, old):
    return round((new - old) / old * 100, 2) if old else None


def _verdict(action: str, d: dict) -> str:
    roas, rev, spend = d.get("roas"), d.get("revenue"), d.get("spend")
    if roas is None:
        return "inconclusive"
    if action == "SCALE_BUDGET":
        good = (rev or 0) > 0 and roas > -15
    elif action in ("CUT_BUDGET", "REDUCE_SPEND", "PAUSE_CAMPAIGN"):
        good = (spend is not None and spend < 0) and roas >= -5
    else:
        good = roas >= 0
    if good:
        return "improved"
    return "worsened" if roas < -5 else "neutral"


def _projection(execution: dict, before: dict) -> dict | None:
    a, p = execution["action"], execution.get("params", {})
    if before["days"] == 0 or before["spend"] == 0:
        return None
    daily = before["spend"] / before["days"]
    if a in ("SCALE_BUDGET", "CUT_BUDGET", "REDUCE_SPEND"):
        pct = p.get("increase_pct" if a == "SCALE_BUDGET" else "decrease_pct",
                    {"SCALE_BUDGET": 20, "CUT_BUDGET": 30, "REDUCE_SPEND": 20}[a])
        sign = 1 if a == "SCALE_BUDGET" else -1
        d_spend = daily * pct / 100 * sign
        d_rev = d_spend * before["roas"] * MARGINAL_FACTOR
        return {"delta_spend_per_day": round(d_spend, 2),
                "delta_revenue_per_day": round(d_rev, 2),
                "assumption": "marginal ROAS = 70% of the pre-action average"}
    if a == "PAUSE_CAMPAIGN":
        return {"delta_spend_per_day": round(-daily, 2),
                "delta_revenue_per_day": round(-before["revenue"] / before["days"], 2),
                "assumption": "all spend and attributed revenue stop"}
    return None


def measure(execution: dict, window_days: int = 3) -> dict:
    ads_all = load_unified()["ads"]
    ads = _target_filter(ads_all, execution["action"], execution["target"])
    if ads.empty:
        return {"found": False, "verdict": "inconclusive",
                "reason": "no data for target"}

    eff = execution.get("params", {}).get("effective_date") or execution["ts"]
    ts = pd.Timestamp(eff)
    ts = (ts.tz_convert(None) if ts.tzinfo else ts).normalize()
    w = pd.Timedelta(days=window_days)
    before = ads[(ads["date"] < ts) & (ads["date"] >= ts - w)]
    after = ads[(ads["date"] >= ts) & (ads["date"] < ts + w)]
    baseline_note = None
    if before.empty and after.empty:
        # action is dated beyond the data: use the latest observed days as baseline
        latest = sorted(ads.loc[ads["date"] < ts, "date"].unique())[-window_days:]
        before = ads[ads["date"].isin(latest)]
        baseline_note = "effective date is after the data; baseline = latest observed days"
    b, a = _agg(before), _agg(after)

    base = {"found": True, "window_days": window_days,
            "target": execution["target"], "action": execution["action"],
            "effective_date": ts.strftime("%Y-%m-%d"),
            "data_range": [ads_all["date"].min().strftime("%Y-%m-%d"),
                           ads_all["date"].max().strftime("%Y-%m-%d")],
            "before": b, "after": a}
    if baseline_note:
        base["baseline_note"] = baseline_note

    if a["days"] == 0:
        return {**base, "verdict": "pending_data", "delta_pct": None,
                "reason": "no observations on/after the effective date yet",
                "projection": _projection(execution, b)}

    delta = {k: _pct(a[k], b[k]) for k in ("spend", "revenue", "roas", "units", "cvr")}
    return {**base, "delta_pct": delta,
            "verdict": _verdict(execution["action"], delta),
            "complete_window": a["days"] >= window_days}
