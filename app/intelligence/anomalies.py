"""Anomaly detection across the unified data.

Every detector derives its windows from the data (no hard-coded dates/SKUs).
Consecutive-day events are merged into a single *episode* finding so the UI
shows one card per incident rather than one per day.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app import config

FUNNEL = [  # (stage, numerator, denominator)
    ("session_to_view",   "product_views",      "sessions"),
    ("view_to_cart",      "add_to_cart",        "product_views"),
    ("cart_to_checkout",  "checkout_initiated", "add_to_cart"),
    ("checkout_to_order", "purchases",          "checkout_initiated"),
]
_SEV_RANK = {"high": 0, "medium": 1, "low": 2}
_TYPE_RANK = {"conversion_crash": 0, "stockout": 1, "platform_shift": 2,
              "creative_fatigue": 3, "attribution_mismatch": 4}


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _episodes(dates: list[pd.Timestamp]) -> list[list[pd.Timestamp]]:
    """Group sorted dates into runs of consecutive calendar days."""
    runs: list[list[pd.Timestamp]] = []
    for d in sorted(set(dates)):
        if runs and (d - runs[-1][-1]).days == 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def _safe_div(a, b) -> float:
    return float(a) / float(b) if b else 0.0


def daily_cvr(ads: pd.DataFrame) -> pd.DataFrame:
    d = ads.groupby("date").agg(clicks=("clicks", "sum"),
                                units=("true_units", "sum"),
                                spend=("spend", "sum"),
                                revenue=("revenue", "sum"))
    d["cvr"] = d["units"] / d["clicks"].replace(0, np.nan)
    return d.fillna({"cvr": 0.0})


def find_crash_days(ads: pd.DataFrame) -> list[pd.Timestamp]:
    """Days whose blended CVR is >CVR_CRASH_DROP_PCT below the median day."""
    d = daily_cvr(ads)
    if len(d) < 4:
        return []
    base = float(d["cvr"].median())
    if base <= 0:
        return []
    return list(d.index[d["cvr"] < base * (1 - config.CVR_CRASH_DROP_PCT)])


# ---------------------------------------------------------------- funnel
def funnel_rates(ga: pd.DataFrame) -> dict[str, float]:
    s = ga[[c for _, c, _ in FUNNEL] + ["sessions"]].sum()
    return {name: _safe_div(s[num], s[den]) for name, num, den in FUNNEL}


def _conversion_crashes(ads, ga, crash_days) -> list[dict]:
    out = []
    d = daily_cvr(ads)
    base = float(d["cvr"].median())
    clean_days = d.index.difference(crash_days)
    base_ga = funnel_rates(ga[ga["date"].isin(clean_days)])
    rev_per_unit = _safe_div(d.loc[clean_days, "revenue"].sum(),
                             d.loc[clean_days, "units"].sum())

    for run in _episodes(list(crash_days)):
        ep = d.loc[run]
        worst_day = ep["cvr"].idxmin()
        drop = 1 - _safe_div(ep["cvr"].min(), base)
        ep_ga = funnel_rates(ga[ga["date"].isin(run)])
        funnel = {}
        for name, _, _ in FUNNEL:
            b, a = base_ga[name], ep_ga[name]
            funnel[name] = {"baseline": round(b, 4), "during": round(a, 4),
                            "change_pct": round(_safe_div(a - b, b), 3)}
        weakest = min(funnel, key=lambda k: funnel[k]["change_pct"])

        # breadth: how many platforms fell >CVR_CRASH_DROP_PCT on the worst day
        day_ads = ads[ads["date"] == worst_day]
        pre_ads = ads[ads["date"].isin(clean_days)]
        plat_now = day_ads.groupby("platform").apply(
            lambda g: _safe_div(g["true_units"].sum(), g["clicks"].sum()),
            include_groups=False)
        plat_base = pre_ads.groupby("platform").apply(
            lambda g: _safe_div(g["true_units"].sum(), g["clicks"].sum()),
            include_groups=False)
        hit = [p for p in plat_now.index
               if plat_base.get(p, 0) and
               plat_now[p] < plat_base[p] * (1 - config.CVR_CRASH_DROP_PCT)]
        lost_units = max(0.0, float((base * ep["clicks"] - ep["units"]).sum()))

        out.append({
            "type": "conversion_crash",
            "date": _iso(run[0]),
            "end_date": _iso(run[-1]),
            "dates": [_iso(x) for x in run],
            "days": len(run),
            "value": round(float(ep["cvr"].min()), 4),
            "worst_date": _iso(worst_day),
            "baseline": round(base, 4),
            "drop_pct": round(drop, 3),
            "severity": "high" if drop >= 0.30 else "medium",
            "funnel": funnel,
            "weakest_stage": weakest,
            "platforms_affected": sorted(hit),
            "platforms_total": int(plat_now.shape[0]),
            "est_lost_units": round(lost_units, 1),
            "est_lost_revenue": round(lost_units * rev_per_unit, 2),
        })
    return out


# --------------------------------------------------------- platform shift
def _roas_parts(g: pd.DataFrame) -> dict[str, float]:
    spend, rev = g["spend"].sum(), g["revenue"].sum()
    clicks, units = g["clicks"].sum(), g["true_units"].sum()
    return {"roas": _safe_div(rev, spend),
            "cpc": _safe_div(spend, clicks),
            "cvr": _safe_div(units, clicks),
            "aov": _safe_div(rev, units)}


def _decompose(pre: dict, post: dict) -> list[dict]:
    """Exact log-decomposition: ln ROAS = -ln CPC + ln CVR + ln AOV."""
    contrib = []
    for name, sign in (("cpc", -1), ("cvr", 1), ("aov", 1)):
        if pre[name] > 0 and post[name] > 0:
            contrib.append((name, sign * math.log(post[name] / pre[name]),
                            _safe_div(post[name] - pre[name], pre[name])))
    contrib.sort(key=lambda t: -abs(t[1]))
    return [{"driver": n, "log_contribution": round(c, 3),
             "metric_change_pct": round(p, 3)} for n, c, p in contrib]


def _platform_shifts(ads, crash_days) -> list[dict]:
    clean = ads[~ads["date"].isin(crash_days)]
    plats = sorted(clean["platform"].unique())
    days_all = sorted(clean["date"].unique())
    k = config.MIN_SEGMENT_DAYS
    if len(days_all) < 2 * k or len(plats) < 1:
        return []

    def roas(g):
        return _safe_div(g["revenue"].sum(), g["spend"].sum())

    best = None  # (split, avg |delta|) -- one fleet-wide change point
    for i in range(k, len(days_all) - k + 1):
        split = days_all[i - 1]
        score = 0.0
        for p in plats:
            g = clean[clean["platform"] == p]
            pre, post = roas(g[g["date"] <= split]), roas(g[g["date"] > split])
            if pre > 0:
                score = max(score, abs(post - pre) / pre)
        if best is None or score > best[1]:
            best = (split, score)
    if best is None:
        return []
    split = best[0]

    deltas = {}
    for p in plats:
        g = clean[clean["platform"] == p]
        pre_g, post_g = g[g["date"] <= split], g[g["date"] > split]
        pre, post = roas(pre_g), roas(post_g)
        if pre > 0:
            deltas[p] = (pre, post, (post - pre) / pre, pre_g, post_g)

    out = []
    for p, (pre, post, delta, pre_g, post_g) in deltas.items():
        others = [v[2] for q, v in deltas.items() if q != p]
        market = float(np.median(others)) if others else 0.0
        rel = delta - market
        if abs(delta) >= config.PLATFORM_SHIFT_PCT and \
                abs(rel) >= config.PLATFORM_RELATIVE_PCT:
            out.append({
                "type": "platform_shift",
                "platform": p,
                "change_date": _iso(clean.loc[clean["date"] > split, "date"].min()),
                "pre_roas": round(pre, 2),
                "post_roas": round(post, 2),
                "delta_pct": round(delta, 3),
                "market_delta_pct": round(market, 3),
                "relative_delta_pct": round(rel, 3),
                "drivers": _decompose(_roas_parts(pre_g), _roas_parts(post_g)),
                "severity": "high" if abs(delta) > 0.30 else "medium",
            })
    return out


# --------------------------------------------------------------- stockout
def _stockouts(ads, inv, sal, ga, mix) -> list[dict]:
    out = []
    flagged = inv[(inv["stockout_flag"] == 1) | (inv["closing_stock"] <= 0)]
    last_day = ads["date"].max()
    for sku, g in flagged.groupby("sku"):
        for run in _episodes(list(g["date"])):
            m = mix[mix["sku"] == sku]
            cids = sorted(m["campaign_id"].unique())
            share = m.set_index("campaign_id")["mix_share"]
            day_ads = ads[ads["date"].isin(run) & ads["campaign_id"].isin(cids)]
            at_risk = float(day_ads["spend"].sum())
            exposed = float((day_ads["spend"]
                             * day_ads["campaign_id"].map(share).fillna(0)).sum())
            wasted_sessions = int(ga[(ga["sku"] == sku)
                                     & ga["date"].isin(run)]["sessions"].sum())
            out.append({
                "type": "stockout",
                "sku": sku,
                "date": _iso(run[0]),
                "end_date": _iso(run[-1]),
                "days": len(run),
                "ongoing": bool(run[-1] >= last_day),
                "campaigns_affected": cids,
                "at_risk_spend": round(at_risk, 2),
                "spend_on_stockout_day": round(exposed, 2),  # exposure-weighted
                "exposed_spend": round(exposed, 2),
                "wasted_sessions": wasted_sessions,
                "severity": "high" if exposed > 100 or run[-1] >= last_day
                            else "medium",
            })
    return out


# ------------------------------------------------------- creative fatigue
def _creative_fatigue(ads, cre) -> list[dict]:
    out = []
    for cid, g in cre.sort_values("date").groupby("creative_id"):
        latest = g.iloc[-1]
        if latest["fatigue_score"] < config.CREATIVE_FATIGUE_MIN:
            continue
        crossed = g[g["fatigue_score"] >= config.CREATIVE_FATIGUE_MIN]["date"].min()
        a = ads[ads["creative_id"] == cid].sort_values("date")
        ctr_drop = cvr_drop = None
        if len(a) >= 6:
            h, t = a.head(3), a.tail(3)
            ctr0 = _safe_div(h["clicks"].sum(), h["impressions"].sum())
            ctr1 = _safe_div(t["clicks"].sum(), t["impressions"].sum())
            cvr0 = _safe_div(h["true_units"].sum(), h["clicks"].sum())
            cvr1 = _safe_div(t["true_units"].sum(), t["clicks"].sum())
            ctr_drop = round(_safe_div(ctr1 - ctr0, ctr0), 3) if ctr0 else None
            cvr_drop = round(_safe_div(cvr1 - cvr0, cvr0), 3) if cvr0 else None
        out.append({
            "type": "creative_fatigue",
            "creative_id": cid,
            "campaign_id": str(latest["campaign_id"]),
            "platform": str(latest["platform"]),
            "date": _iso(crossed),
            "latest_date": _iso(latest["date"]),
            "fatigue_score": round(float(latest["fatigue_score"]), 3),
            "age_days": int(latest["age_days"]),
            "ctr_change_pct": ctr_drop,
            "cvr_change_pct": cvr_drop,
            "severity": "high" if latest["fatigue_score"] >= 0.8 else "medium",
        })
    return out


# ------------------------------------------------------------ attribution
def _attribution(ads, sal) -> list[dict]:
    rep = ads.groupby("campaign_id")["conversions"].sum()
    act = sal.groupby("campaign_id")["units_sold"].sum()
    plat = ads.groupby("campaign_id")["platform"].first()
    out = []
    for cid in rep.index.union(act.index):
        r, a = float(rep.get(cid, 0)), float(act.get(cid, 0))
        if a == 0:
            continue
        gap = (r - a) / a
        if abs(gap) >= config.ATTRIBUTION_GAP_PCT:
            out.append({
                "type": "attribution_mismatch",
                "campaign_id": cid,
                "platform": str(plat.get(cid, "")),
                "reported": int(r),
                "actual": int(a),
                "gap_pct": round(gap, 3),
                "severity": "high" if abs(gap) > 0.30 else "medium",
            })
    return out


# ------------------------------------------------------------------ main
def detect_anomalies(unified: dict) -> list[dict]:
    ads, inv = unified["ads"], unified["inventory"]
    cre, sal = unified["creative"], unified["sales"]
    ga, mix = unified["ga_events"], unified["campaign_sku_mix"]

    crash_days = find_crash_days(ads)
    findings: list[dict] = []
    findings += _conversion_crashes(ads, ga, crash_days) if crash_days else []
    findings += _platform_shifts(ads, crash_days)
    findings += _stockouts(ads, inv, sal, ga, mix)
    findings += _creative_fatigue(ads, cre)
    findings += _attribution(ads, sal)

    # cross-link findings that plausibly feed each other (e.g. a fatigued
    # creative running on a platform whose ROAS fell)
    for f in findings:
        if f["type"] == "platform_shift":
            f["related"] = [g["creative_id"] for g in findings
                            if g["type"] == "creative_fatigue"
                            and g["platform"] == f["platform"]]
            camps = set(ads.loc[ads["platform"] == f["platform"], "campaign_id"])
            f["related_stockouts"] = [g["sku"] for g in findings
                                      if g["type"] == "stockout"
                                      and camps & set(g["campaigns_affected"])]

    for f in findings:
        key = (f.get("sku") or f.get("creative_id") or f.get("campaign_id")
               or f.get("platform") or "site")
        day = f.get("date") or f.get("change_date") or ""
        f["id"] = f"{f['type']}:{key}:{day}"
    findings.sort(key=lambda f: (_SEV_RANK.get(f["severity"], 9),
                                 _TYPE_RANK.get(f["type"], 9),
                                 f.get("date", "")))
    return findings
