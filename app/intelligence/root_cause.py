"""Evidence-driven root-cause reasoning.

Rules read the numbers attached to each finding (funnel stage deltas, log-ROAS
driver decomposition, cross-platform breadth, ...) rather than emitting canned
text, and return a ranked cause with a confidence value.
"""

_STAGE_CAUSE = {
    "checkout_to_order": ("Checkout / payment failure",
                          ["Payment gateway status & decline rate",
                           "Checkout error logs / recent releases",
                           "Tag manager / purchase-event firing"]),
    "cart_to_checkout": ("Cart-to-checkout friction (shipping cost, forced "
                         "login, broken cart page)",
                         ["Cart page errors", "Shipping/tax display changes"]),
    "view_to_cart": ("Product-page degradation (pricing, stock messaging, "
                     "page speed)",
                     ["Product page load time", "Price or promo changes"]),
    "session_to_view": ("Landing / traffic-quality issue",
                        ["Landing-page redirects", "Traffic source mix"]),
}
_DRIVER_TEXT = {
    "cpc": "traffic got more expensive (CPC up)",
    "cvr": "click-to-order conversion fell",
    "aov": "revenue per order fell (mix / discounting)",
}


def _pct(x) -> str:
    return f"{x:+.0%}"


def explain(f: dict) -> dict:
    t = f["type"]

    if t == "conversion_crash":
        stage = f.get("weakest_stage")
        cause, checks = _STAGE_CAUSE.get(
            stage, ("Sitewide conversion path degradation", []))
        fs = f.get("funnel", {}).get(stage, {})
        n_hit, n_all = len(f.get("platforms_affected", [])), f.get("platforms_total", 0)
        sitewide = n_all > 0 and n_hit >= max(2, n_all - 1)
        evidence = [
            f"Blended CVR {f['value']} vs median {f['baseline']} "
            f"({_pct(-f['drop_pct'])}) over {f['days']} day(s)",
        ]
        if fs:
            evidence.append(f"Funnel stage '{stage}' moved {_pct(fs['change_pct'])} "
                            f"({fs['baseline']} -> {fs['during']}); other stages "
                            "were comparatively stable")
        evidence.append(f"{n_hit}/{n_all} platforms dropped simultaneously "
                        f"-> {'sitewide, not channel-specific' if sitewide else 'partly channel-specific'}")
        conf = 0.55 + (0.2 if sitewide else 0) + \
            (0.2 if fs and fs.get("change_pct", 0) < -0.3 else 0)
        return {"category": "site", "probable_cause": cause,
                "confidence": round(min(conf, 0.95), 2), "evidence": evidence,
                "next_checks": checks,
                "impact": {"est_lost_revenue": f.get("est_lost_revenue"),
                           "est_lost_units": f.get("est_lost_units")}}

    if t == "platform_shift":
        drivers = f.get("drivers", [])
        top = drivers[0] if drivers else None
        direction = "down" if f["delta_pct"] < 0 else "up"
        specific = abs(f.get("relative_delta_pct", 0)) >= 0.10
        evidence = [f"ROAS {f['pre_roas']} -> {f['post_roas']} ({_pct(f['delta_pct'])}) "
                    f"from {f.get('change_date')}",
                    f"Other platforms moved {_pct(f.get('market_delta_pct', 0))} "
                    f"over the same split (relative {_pct(f.get('relative_delta_pct', 0))})"]
        if top:
            evidence.append(f"Largest driver: {_DRIVER_TEXT[top['driver']]} "
                            f"({_pct(top['metric_change_pct'])})")
        cause = f"{f['platform']} auction / algorithm dynamics shifted {direction}"
        if top and top["driver"] == "aov":
            cause = (f"{f['platform']} is delivering lower-value orders "
                     f"(audience/mix shift), not a site problem")
        elif top and top["driver"] == "cpc":
            cause = f"{f['platform']} auction pressure: cost per click rose"
        if f.get("related"):
            evidence.append("Contributing: fatigued creative(s) "
                            f"{', '.join(f['related'])} run on this platform")
        if f.get("related_stockouts"):
            evidence.append("Contributing: stocked-out SKU(s) "
                            f"{', '.join(f['related_stockouts'])} sold via "
                            "campaigns on this platform")
        if top and top["driver"] == "cvr" and f.get("related"):
            cause = (f"{f['platform']} conversion decline concentrated on "
                     f"fatigued creative ({', '.join(f['related'])}) - "
                     "audience saturation rather than the auction")
        return {"category": "platform",
                "probable_cause": cause,
                "confidence": round(0.8 if specific else 0.5, 2),
                "evidence": evidence,
                "next_checks": ["Frequency & audience overlap",
                                "Bid strategy / cap changes",
                                "Competitor auction pressure"]}

    if t == "stockout":
        return {"category": "inventory",
                "probable_cause": "Ad pacing is not synced with ERP stock - spend "
                                  "continues on a SKU that cannot be fulfilled",
                "confidence": 0.9,
                "evidence": [
                    f"{f['sku']} out of stock {f['date']} -> {f['end_date']} "
                    f"({f['days']}d{', still out' if f.get('ongoing') else ''})",
                    f"Campaigns selling it: {', '.join(f['campaigns_affected'])}",
                    f"Exposure-weighted spend: ${f['exposed_spend']:,.0f} "
                    f"(full campaign spend ${f['at_risk_spend']:,.0f})",
                    f"{f['wasted_sessions']:,} sessions landed on the SKU"],
                "next_checks": ["Replenishment lead time",
                                "Real-time stock-based pacing rule"]}

    if t == "creative_fatigue":
        ev = [f"fatigue_score={f['fatigue_score']} (age {f['age_days']}d), "
              f"first above threshold {f['date']}"]
        if f.get("ctr_change_pct") is not None:
            ev.append(f"CTR {_pct(f['ctr_change_pct'])}, "
                      f"CVR {_pct(f.get('cvr_change_pct') or 0)} since launch")
        return {"category": "creative",
                "probable_cause": "Audience saturation on an aging creative",
                "confidence": 0.85, "evidence": ev,
                "next_checks": ["Frequency cap", "Rotate new variants"]}

    if t == "attribution_mismatch":
        direction = "over-reports" if f["gap_pct"] > 0 else "under-reports"
        return {"category": "measurement",
                "probable_cause": f"{f.get('platform') or 'The platform'} "
                                  f"{direction} conversions vs the sales ledger "
                                  "(attribution window / view-through credit)",
                "confidence": 0.7,
                "evidence": [f"reported={f['reported']} vs ledger={f['actual']} "
                             f"({_pct(f['gap_pct'])})"],
                "next_checks": ["Post-click / view-through window",
                                "Cross-device tracking",
                                "Coupon / promo code usage"]}

    return {"category": "unknown", "probable_cause": "unknown", "confidence": 0.0,
            "evidence": [], "next_checks": []}
