"""Ingest + reconcile multi-source data into a unified, validated frame.

Sources: ad platforms (spend), eCommerce sales ledger, Google Analytics funnel,
ERP inventory, SKU margins, creative telemetry and audience reach.
"""
from __future__ import annotations

import json
import threading

import pandas as pd

from app import config
from app.utils.errors import DataMissing

REQUIRED: dict[str, list[str]] = {
    "ad_spend.csv": ["date", "campaign_id", "platform", "creative_id", "spend",
                     "impressions", "clicks", "conversions", "true_units", "revenue"],
    "sales.csv": ["date", "campaign_id", "sku", "units_sold", "revenue"],
    "inventory.csv": ["date", "sku", "opening_stock", "units_sold",
                      "closing_stock", "stockout_flag"],
    "sku_margins.csv": ["sku", "category", "price", "cost", "margin_pct"],
    "creative_assets.csv": ["date", "creative_id", "campaign_id", "platform",
                            "age_days", "thumb_stop_rate", "engagement",
                            "fatigue_score"],
    "ga_events.csv": ["date", "campaign_id", "sku", "sessions", "product_views",
                      "add_to_cart", "checkout_initiated", "purchases"],
    "audience_segments.csv": ["date", "campaign_id", "platform", "reach",
                              "frequency", "impressions"],
}

_lock = threading.Lock()
_cache: dict = {"sig": None, "val": None}


def _read(name: str) -> pd.DataFrame:
    path = config.DATA_DIR / name
    if not path.exists():
        raise DataMissing(f"Required data file missing: {name}",
                          {"path": str(path)})
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # malformed CSV
        raise DataMissing(f"Could not parse {name}: {exc}", {"file": name})
    missing = [c for c in REQUIRED[name] if c not in df.columns]
    if missing:
        raise DataMissing(f"{name} is missing columns {missing}",
                          {"file": name, "missing": missing})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        if df["date"].isna().any():
            raise DataMissing(f"{name} contains unparseable dates", {"file": name})
    return df


def _signature() -> tuple:
    sig = []
    for name in REQUIRED:
        p = config.DATA_DIR / name
        sig.append((name, p.stat().st_mtime_ns, p.stat().st_size)
                   if p.exists() else (name, None, None))
    return tuple(sig)


def load_raw() -> dict[str, pd.DataFrame]:
    return {
        "ads":       _read("ad_spend.csv"),
        "sales":     _read("sales.csv"),
        "inventory": _read("inventory.csv"),
        "margins":   _read("sku_margins.csv"),
        "creative":  _read("creative_assets.csv"),
        "ga_events": _read("ga_events.csv"),
        "audience":  _read("audience_segments.csv"),
    }


def _build_unified() -> dict[str, pd.DataFrame]:
    raw = load_raw()
    ads, sales = raw["ads"], raw["sales"]
    inv, mar, cre = raw["inventory"], raw["margins"], raw["creative"]

    # --- creative telemetry: one row per (date, creative) -> join on BOTH keys
    cre = cre.drop_duplicates(["date", "creative_id"], keep="last")
    ads = ads.merge(
        cre[["date", "creative_id", "age_days", "thumb_stop_rate",
             "engagement", "fatigue_score"]],
        on=["date", "creative_id"], how="left", validate="many_to_one",
    )
    if len(ads) != len(raw["ads"]):
        raise DataMissing("creative join changed the ad row count",
                          {"before": len(raw["ads"]), "after": len(ads)})

    # --- audience reach / frequency per (date, campaign)
    aud = raw["audience"].drop_duplicates(["date", "campaign_id"], keep="last")
    ads = ads.merge(aud[["date", "campaign_id", "reach", "frequency"]],
                    on=["date", "campaign_id"], how="left")

    # --- per-campaign SKU mix (revenue share) and weighted margin
    sal = sales.merge(mar[["sku", "margin_pct", "price", "cost"]],
                      on="sku", how="left")
    sal["gross_profit"] = sal["revenue"] * sal["margin_pct"]
    mix = (sal.groupby(["campaign_id", "sku"], as_index=False)
              .agg(revenue=("revenue", "sum"), units=("units_sold", "sum"),
                   gross_profit=("gross_profit", "sum")))
    tot = mix.groupby("campaign_id")["revenue"].transform("sum")
    mix["mix_share"] = (mix["revenue"] / tot).where(tot > 0, 0.0)

    meta_rows = []
    for cid, g in mix.groupby("campaign_id"):
        top = g.loc[g["revenue"].idxmax()]
        rev = g["revenue"].sum()
        meta_rows.append({
            "campaign_id": cid,
            "dominant_sku": top["sku"],
            "weighted_margin_pct": float(g["gross_profit"].sum() / rev) if rev else 0.0,
        })
    camp_meta = pd.DataFrame(meta_rows, columns=["campaign_id", "dominant_sku",
                                                 "weighted_margin_pct"])
    ads = ads.merge(camp_meta, on="campaign_id", how="left")

    # --- inventory joins (vectorised)
    inv_k = inv[["date", "sku", "closing_stock", "stockout_flag"]]
    ads = ads.merge(
        inv_k.rename(columns={"sku": "dominant_sku",
                              "closing_stock": "dominant_sku_stock"})
             [["date", "dominant_sku", "dominant_sku_stock"]],
        on=["date", "dominant_sku"], how="left")

    # share of the campaign's revenue mix that is out of stock on that date
    oos = inv_k.assign(oos=((inv_k["stockout_flag"] == 1)
                            | (inv_k["closing_stock"] <= 0)).astype(float))
    exp = (mix[["campaign_id", "sku", "mix_share"]]
           .merge(oos[["date", "sku", "oos"]], on="sku", how="inner"))
    exp["w"] = exp["mix_share"] * exp["oos"]
    exp = exp.groupby(["date", "campaign_id"], as_index=False)["w"].sum() \
             .rename(columns={"w": "oos_exposure"})
    ads = ads.merge(exp, on=["date", "campaign_id"], how="left")
    ads["oos_exposure"] = ads["oos_exposure"].fillna(0.0).clip(0, 1)

    return {
        "ads": ads.sort_values(["date", "campaign_id"]).reset_index(drop=True),
        "sales": sales,
        "inventory": inv,
        "margins": mar,
        "creative": cre,
        "ga_events": raw["ga_events"],
        "audience": raw["audience"],
        "campaign_meta": camp_meta,
        "campaign_sku_mix": mix,
    }


def load_unified() -> dict[str, pd.DataFrame]:
    """Cached by file mtime/size; callers get private copies."""
    sig = _signature()
    with _lock:
        if _cache["sig"] != sig or _cache["val"] is None:
            _cache["val"] = _build_unified()
            _cache["sig"] = sig
        return {k: v.copy() for k, v in _cache["val"].items()}


def to_records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records: dates -> ISO strings, NaN/inf -> null."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out = out.replace([float("inf"), float("-inf")], float("nan"))
    return json.loads(out.to_json(orient="records"))
