"""Time-series endpoints the frontend can poll / animate."""
import numpy as np
import pandas as pd
from fastapi import APIRouter

from app.data.loader import load_unified, to_records
from app.intelligence.anomalies import FUNNEL, detect_anomalies

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _kpis(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()
    g["roas"] = (g["revenue"] / g["spend"].replace(0, np.nan)).round(2)
    g["cvr"] = (g["units"] / g["clicks"].replace(0, np.nan)).round(4)
    return g.fillna({"roas": 0.0, "cvr": 0.0})


def _agg(ads: pd.DataFrame, by) -> pd.DataFrame:
    return _kpis(ads.groupby(by).agg(
        spend=("spend", "sum"), revenue=("revenue", "sum"),
        clicks=("clicks", "sum"), units=("true_units", "sum")).reset_index())


@router.get("/daily")
def daily():
    return to_records(_agg(load_unified()["ads"], "date"))


@router.get("/by-platform")
def by_platform():
    return to_records(_agg(load_unified()["ads"], "platform"))


@router.get("/by-campaign")
def by_campaign():
    u = load_unified()
    g = _agg(u["ads"], ["campaign_id", "platform"])
    g = g.merge(u["campaign_meta"], on="campaign_id", how="left")
    g["gross_profit"] = (g["revenue"] * g["weighted_margin_pct"]).round(2)
    g["net_profit"] = (g["gross_profit"] - g["spend"]).round(2)
    return to_records(g)


@router.get("/funnel")
def funnel():
    """Daily GA funnel with stage conversion rates."""
    ga = load_unified()["ga_events"]
    cols = ["sessions", "product_views", "add_to_cart", "checkout_initiated", "purchases"]
    d = ga.groupby("date")[cols].sum().reset_index()
    for name, num, den in FUNNEL:
        d[name] = (d[num] / d[den].replace(0, np.nan)).round(4)
    return to_records(d.fillna(0))


@router.get("/timeline")
def timeline():
    """Day-by-day KPIs with the incidents active on each day: one call is
    enough to drive a replay / animation of the whole period."""
    u = load_unified()
    findings = detect_anomalies(u)
    days = _agg(u["ads"], "date")
    recs = to_records(days)
    for r in recs:
        r["events"] = []
    by_date = {r["date"]: r for r in recs}
    for f in findings:
        if f["type"] in ("conversion_crash", "stockout"):
            active = f.get("dates") or [d for d in by_date
                                        if f["date"] <= d <= f["end_date"]]
        elif f["type"] == "creative_fatigue":
            active = [d for d in by_date if f["date"] <= d <= f["latest_date"]]
        elif f["type"] == "platform_shift":
            active = [d for d in by_date if d >= f["change_date"]]
        else:
            continue
        for d in active:
            if d in by_date:
                by_date[d]["events"].append(
                    {"id": f["id"], "type": f["type"], "severity": f["severity"]})
    return recs
