"""Decision engine: prioritised, guard-railed, explainable actions.

Guardrails (why a campaign is *not* scaled): out-of-stock exposure, fatigued
creative, thin stock cover, margin floor, or a platform currently in a
down-shift (we don't pour budget into a deteriorating auction).
Every action carries a confidence, an expected impact (with its assumption),
the evidence ids that triggered it, and whether it may be auto-executed.
"""
from __future__ import annotations

from app import config
from app.learning.feedback import learned_adjustments

MARGINAL_FACTOR = 0.7     # marginal ROAS assumed = 70% of average (diminishing returns)
_PRIO = {"high": 0, "medium": 1, "low": 2}


def _step(base: int, action: str, learned: dict) -> tuple[int, str | None]:
    adj = learned.get(action)
    if not adj or adj.get("step_multiplier", 1.0) == 1.0:
        return base, None
    pct = max(5, int(round(base * adj["step_multiplier"])))
    return pct, (f"step scaled x{adj['step_multiplier']} from "
                 f"{adj['n']} past outcomes ({adj['success_rate']:.0%} success)")


def _mk(action, target, rationale, priority, confidence, *, impact=None,
        evidence=None, auto=False, extra=None) -> dict:
    d = {
        "id": f"{action}:{target}",
        "action": action,
        "target": target,
        "rationale": rationale,
        "priority": priority,
        "confidence": round(float(confidence), 2),
        "status": "proposed",
        "auto_approvable": bool(auto),
        "evidence": evidence or [],
    }
    if impact:
        d["expected_impact"] = impact
    if extra:
        d.update(extra)
    return d


def decide(diagnosis: list[dict], opportunities: list[dict],
           learned: dict | None = None) -> list[dict]:
    learned = learned_adjustments() if learned is None else learned
    actions: list[dict] = []
    opp = {o["campaign_id"]: o for o in opportunities}
    paused: set[str] = set()

    # --- platforms in a down-shift (no new money until it stabilises)
    held = {f["platform"]: f for f in diagnosis
            if f["type"] == "platform_shift" and f["delta_pct"] < -0.20}

    # 1) inventory: stop paying for clicks we cannot fulfil
    for o in opportunities:
        exp = o.get("oos_exposure", 0.0)
        if exp <= 0:
            continue
        ev = [f["id"] for f in diagnosis if f["type"] == "stockout"
              and o["campaign_id"] in f.get("campaigns_affected", [])]
        wasted = o.get("daily_spend", 0) * exp
        impact = {"metric": "wasted_spend_avoided_per_day",
                  "value": round(wasted, 2),
                  "assumption": "daily spend x share of revenue mix that is out of stock"}
        if exp >= 0.5:
            paused.add(o["campaign_id"])
            actions.append(_mk(
                "PAUSE_CAMPAIGN", o["campaign_id"],
                f"{exp:.0%} of this campaign's revenue mix is out of stock "
                f"(dominant SKU {o['dominant_sku']}) - spend cannot convert",
                "high", 0.9, impact=impact, evidence=ev, auto=True))
        else:
            pct, note = _step(max(20, int(round(exp * 100))), "REDUCE_SPEND", learned)
            actions.append(_mk(
                "REDUCE_SPEND", o["campaign_id"],
                f"{exp:.0%} of revenue mix is out of stock; trim spend until "
                "replenished",
                "high", 0.85, impact=impact, evidence=ev, auto=True,
                extra={"suggested_decrease_pct": pct,
                       **({"learning_note": note} if note else {})}))

    # 2) site-level conversion incident
    for f in diagnosis:
        if f["type"] == "conversion_crash":
            actions.append(_mk(
                "INVESTIGATE_SITE", "site",
                f"Blended CVR fell {f['drop_pct']:.0%} on {f['date']}"
                f"{' to ' + f['end_date'] if f['end_date'] != f['date'] else ''}; "
                f"funnel stage '{f['weakest_stage']}' collapsed on "
                f"{len(f['platforms_affected'])}/{f['platforms_total']} platforms",
                "high", 0.9,
                impact={"metric": "revenue_lost_in_incident",
                        "value": f["est_lost_revenue"],
                        "assumption": "median CVR x clicks x baseline revenue/unit"},
                evidence=[f["id"]]))

    # 3) platform down-shift
    for plat, f in held.items():
        deferred = [o["campaign_id"] for o in opportunities if o["platform"] == plat
                    and o["opportunity_score"] >= config.SCALE_SCORE_MIN]
        driver = f["drivers"][0]["driver"] if f.get("drivers") else "n/a"
        actions.append(_mk(
            "HOLD_PLATFORM_SPEND", plat,
            f"ROAS {f['pre_roas']} -> {f['post_roas']} ({f['delta_pct']:+.0%}) "
            f"vs market {f['market_delta_pct']:+.0%}; main driver: {driver}"
            + (f"; scaling deferred for {', '.join(deferred)}" if deferred else ""),
            "high" if f["severity"] == "high" else "medium",
            0.8 if abs(f["relative_delta_pct"]) >= 0.2 else 0.6,
            evidence=[f["id"]], auto=False))

    # 4) fatigued creatives
    for f in diagnosis:
        if f["type"] == "creative_fatigue":
            actions.append(_mk(
                "REFRESH_CREATIVE", f["creative_id"],
                f"fatigue {f['fatigue_score']} at age {f['age_days']}d on "
                f"{f['campaign_id']} ({f['platform']})"
                + (f"; CTR {f['ctr_change_pct']:+.0%}" if f.get("ctr_change_pct") is not None else ""),
                "high" if f["severity"] == "high" else "medium", 0.85,
                evidence=[f["id"]], auto=False))

    # 5) low-ROAS budget cuts
    for o in opportunities:
        if o["campaign_id"] in paused:
            continue
        if max(o["roas"], o["recent_roas"]) < config.ROAS_PAUSE_THRESHOLD:
            pct, note = _step(config.CUT_STEP_PCT, "CUT_BUDGET", learned)
            saved = o["daily_spend"] * pct / 100
            actions.append(_mk(
                "CUT_BUDGET", o["campaign_id"],
                f"ROAS {o['roas']} (recent {o['recent_roas']}) below "
                f"{config.ROAS_PAUSE_THRESHOLD}; contribution ROAS "
                f"{o['contribution_roas']}",
                "medium", 0.75,
                impact={"metric": "profit_per_day", "value": round(
                    saved * (1 - MARGINAL_FACTOR * o["contribution_roas"]), 2),
                    "assumption": "marginal contribution ROAS = 70% of average"},
                extra={"suggested_decrease_pct": pct,
                       **({"learning_note": note} if note else {})}))

    # 6) scale winners (guard-railed)
    for o in opportunities:
        cid = o["campaign_id"]
        if cid in paused or cid in {a["target"] for a in actions
                                    if a["action"] in ("CUT_BUDGET", "REDUCE_SPEND")}:
            continue
        ok = (o["opportunity_score"] >= config.SCALE_SCORE_MIN
              and min(o["roas"], o["recent_roas"]) >= config.ROAS_SCALE_THRESHOLD
              and o["weighted_margin_pct"] >= config.MARGIN_MIN
              and o["stock_headroom"] > config.STOCK_HEADROOM_MIN
              and o["stock_cover_days"] >= config.STOCK_COVER_MIN_DAYS
              and o["oos_exposure"] == 0
              and o["creative_fatigue"] < config.CREATIVE_FATIGUE_MIN
              and o["platform"] not in held)
        if not ok:
            continue
        pct, note = _step(config.SCALE_STEP_PCT, "SCALE_BUDGET", learned)
        gain = o["daily_spend"] * pct / 100 * (MARGINAL_FACTOR * o["contribution_roas"] - 1)
        conf = min(0.5 + 0.5 * o["opportunity_score"] * o["attribution_trust"], 0.95)
        actions.append(_mk(
            "SCALE_BUDGET", cid,
            f"ROAS {o['roas']} (recent {o['recent_roas']}) >= "
            f"{config.ROAS_SCALE_THRESHOLD}, margin {o['weighted_margin_pct']:.0%}, "
            f"{o['stock_headroom']} units / {o['stock_cover_days']}d of stock cover",
            "high" if o["opportunity_score"] >= 0.8 else "medium", conf,
            impact={"metric": "profit_per_day", "value": round(gain, 2),
                    "assumption": "marginal contribution ROAS = 70% of average"},
            auto=(pct <= 20 and conf >= 0.85 and o["attribution_trust"] >= 0.9),
            extra={"suggested_increase_pct": pct,
                   **({"learning_note": note} if note else {})}))

    # 7) measurement hygiene
    for f in diagnosis:
        if f["type"] == "attribution_mismatch":
            actions.append(_mk(
                "INVESTIGATE_ATTRIBUTION", f["campaign_id"],
                f"{f['platform']} reports {f['reported']} conversions vs "
                f"{f['actual']} in the ledger ({f['gap_pct']:+.0%}); trust the "
                "ledger for budgeting",
                "low", 0.7, evidence=[f["id"]], auto=True))

    actions.sort(key=lambda a: (_PRIO[a["priority"]],
                                -(a.get("expected_impact", {}).get("value") or 0),
                                -a["confidence"]))
    return actions
