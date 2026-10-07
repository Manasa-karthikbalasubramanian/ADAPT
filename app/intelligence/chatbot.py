"""Data-grounded chat bot.

Every reply is built from a compact snapshot of the live dataset, so
answers reference real campaign IDs, ROAS values, and anomalies - not
hallucinations.
"""
from __future__ import annotations
import json
from app.data.loader import load_unified
from app.intelligence.anomalies import detect_anomalies
from app.intelligence.opportunity import score_opportunities
from app.intelligence import llm_provider


SYSTEM = (
    "You are ADAPT, a D2C marketing intelligence assistant. "
    "Answer ONLY using the data snapshot provided. "
    "If the snapshot does not contain the answer, say what is missing. "
    "Be concise, specific, and use real campaign IDs and numbers. "
    "Max 180 words."
)


def snapshot() -> dict:
    unified = load_unified()
    ads = unified["ads"]
    total_spend = float(ads["spend"].sum())
    total_rev   = float(ads["revenue"].sum())
    blended_roas = round(total_rev / total_spend, 2) if total_spend else 0.0

    daily = (ads.assign(d=ads["date"].dt.strftime("%Y-%m-%d"))
                .groupby("d").agg(spend=("spend", "sum"),
                                  revenue=("revenue", "sum")).reset_index())
    daily["roas"] = (daily["revenue"] / daily["spend"].replace(0, 1)).round(2)

    plat = (ads.groupby("platform").agg(spend=("spend", "sum"),
                                        revenue=("revenue", "sum")).reset_index())
    plat["roas"] = (plat["revenue"] / plat["spend"].replace(0, 1)).round(2)
    plat_rows = plat.round(2).to_dict(orient="records")

    return {
        "blended_roas": blended_roas,
        "total_spend": round(total_spend, 2),
        "total_revenue": round(total_rev, 2),
        "date_range": [daily["d"].min(), daily["d"].max()],
        "daily_tail": daily.tail(7).to_dict(orient="records"),
        "platforms": plat_rows,
        "anomalies": detect_anomalies(unified),
        "top_opportunities": score_opportunities(unified)[:8],
    }


def answer(question: str, history: list[dict] | None = None) -> dict:
    snap = snapshot()
    if not llm_provider.available():
        return {
            "provider": "template",
            "answer": _template_answer(question, snap),
            "grounded": True,
        }

    history_text = ""
    if history:
        for h in history[-4:]:
            role = h.get("role", "user")
            history_text += f"{role}: {h.get('content','')}\n"

    prompt = (
        "DATA SNAPSHOT (JSON):\n"
        f"{json.dumps(snap, default=str)[:6000]}\n\n"
        f"CONVERSATION SO FAR:\n{history_text}\n"
        f"USER QUESTION: {question}\n\n"
        "Answer using only the snapshot."
    )

    try:
        text = llm_provider.complete(prompt=prompt, system=SYSTEM,
                                     temperature=0.2, max_tokens=400)
    except Exception as e:
        return {"provider": "error", "answer": f"LLM error: {e}",
                "grounded": False}
    return {"provider": llm_provider.active_provider(),
            "answer": text, "grounded": True}


def _template_answer(question: str, snap: dict) -> str:
    q = question.lower()
    if "roas" in q or "performance" in q:
        return (f"Blended ROAS is **{snap['blended_roas']}** "
                f"(${snap['total_revenue']:,.0f} revenue / "
                f"${snap['total_spend']:,.0f} spend). "
                f"Platform breakdown: {snap['platforms']}")
    if "anomaly" in q or "problem" in q or "wrong" in q:
        return f"{len(snap['anomalies'])} anomalies detected: {snap['anomalies'][:5]}"
    if "opportunit" in q or "scale" in q or "grow" in q:
        return f"Top opportunities: {snap['top_opportunities'][:3]}"
    return ("LLM not configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY, "
            "then restart. Snapshot ready: "
            f"blended ROAS {snap['blended_roas']}, "
            f"{len(snap['anomalies'])} anomalies.")
