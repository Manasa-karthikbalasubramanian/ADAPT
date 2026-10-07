"""Predictive opportunity scoring (profit-aware, stock-aware, recency-weighted)."""
from __future__ import annotations

import math

import pandas as pd

from app import config
from app.intelligence.anomalies import find_crash_days

RECENT_DAYS = 3


def _div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _sku_cover(inv: pd.DataFrame) -> pd.DataFrame:
    """Latest closing stock and days of cover per SKU."""
    inv = inv.sort_values("date")
    last_day = inv["date"].max()
    rows = []
    for sku, g in inv.groupby("sku"):
        latest = g.iloc[-1]
        stock = max(float(latest["closing_stock"]), 0.0)
        if latest["date"] < last_day:       # stale record -> treat as unknown
            stock = float("nan")
        demand = float(g.tail(RECENT_DAYS)["units_sold"].mean())
        cover = 0.0 if stock <= 0 else (stock / demand if demand > 0 else 99.0)
        rows.append({"sku": sku, "stock": stock,
                     "cover_days": min(cover, 99.0)})
    return pd.DataFrame(rows).set_index("sku")


def score_opportunities(unified: dict) -> list[dict]:
    ads_all = unified["ads"]
    sal, inv = unified["sales"], unified["inventory"]
    mix = unified["campaign_sku_mix"]
    last_day = ads_all["date"].max()

    crash = set(find_crash_days(ads_all))
    ads = ads_all[~ads_all["date"].isin(crash)]
    sku_cover = _sku_cover(inv)

    rows: list[dict] = []
    for cid, g in ads.groupby("campaign_id"):
        g = g.sort_values("date")
        spend, rev = float(g["spend"].sum()), float(g["revenue"].sum())
        roas = _div(rev, spend)
        recent = g.tail(RECENT_DAYS)
        recent_roas = _div(float(recent["revenue"].sum()),
                           float(recent["spend"].sum()), roas)
        # recency-weighted view: don't scale on stale glory
        blended_roas = 0.4 * roas + 0.6 * recent_roas

        m_val = g["weighted_margin_pct"].iloc[0]
        margin = float(m_val) if pd.notna(m_val) else 0.0
        contribution_roas = blended_roas * margin
        profit = rev * margin - spend

        cvr_recent = _div(float(recent["true_units"].sum()),
                          max(float(recent["clicks"].sum()), 1))
        cvr_all = _div(float(g["true_units"].sum()),
                       max(float(g["clicks"].sum()), 1))
        cvr_trend = _div(cvr_recent - cvr_all, cvr_all)

        actual = float(sal.loc[sal["campaign_id"] == cid, "units_sold"].sum())
        reported = float(g["conversions"].sum())
        # compare like-for-like (all days) when judging attribution
        reported_all = float(ads_all.loc[ads_all["campaign_id"] == cid,
                                         "conversions"].sum())
        trust = max(0.0, 1.0 - abs(reported_all - actual) / actual) if actual else 1.0

        # ---- stock: consider every SKU that is >=15% of the campaign mix
        m = mix[(mix["campaign_id"] == cid) & (mix["mix_share"] >= 0.15)]
        stocks, covers = [], []
        for sku in m["sku"]:
            if sku in sku_cover.index:
                s_ = sku_cover.loc[sku, "stock"]
                if pd.notna(s_):
                    stocks.append(float(s_))
                    covers.append(float(sku_cover.loc[sku, "cover_days"]))
        stock_headroom = min(stocks) if stocks else 0.0
        cover_days = min(covers) if covers else 0.0
        oos_now = float(ads_all.loc[(ads_all["campaign_id"] == cid)
                                    & (ads_all["date"] == last_day),
                                    "oos_exposure"].max()) if len(ads_all) else 0.0
        oos_now = 0.0 if math.isnan(oos_now) else oos_now

        fat = g["fatigue_score"].dropna()
        fatigue_now = float(fat.iloc[-1]) if len(fat) else 0.0

        headroom_score = min(stock_headroom / 500.0, 1.0)
        score = (
            0.35 * min(contribution_roas / 6.0, 1.0)
            + 0.20 * margin
            + 0.15 * min(max(cvr_trend + 0.5, 0.0), 1.0)
            + 0.15 * headroom_score
            + 0.15 * trust
        )
        # hard penalties for risks that make extra spend wasteful
        score *= (1 - 0.6 * oos_now)
        score *= (1 - 0.4 * max(0.0, fatigue_now - config.CREATIVE_FATIGUE_MIN)
                  / (1 - config.CREATIVE_FATIGUE_MIN))

        rows.append({
            "campaign_id": cid,
            "platform": str(g["platform"].iloc[0]),
            "roas": round(roas, 2),
            "recent_roas": round(recent_roas, 2),
            "contribution_roas": round(contribution_roas, 2),
            "weighted_margin_pct": round(margin, 3),
            "net_profit": round(profit, 2),
            "spend": round(spend, 2),
            "daily_spend": round(_div(spend, g["date"].nunique()), 2),
            "cvr_trend": round(cvr_trend, 3),
            "stock_headroom": int(stock_headroom),
            "stock_cover_days": round(cover_days, 1),
            "oos_exposure": round(oos_now, 3),
            "creative_fatigue": round(fatigue_now, 3),
            "dominant_sku": str(g["dominant_sku"].iloc[0]),
            "attribution_trust": round(trust, 3),
            "opportunity_score": round(float(score), 3),
        })

    rows.sort(key=lambda r: -r["opportunity_score"])
    return rows
