"""LLM narrators for pipeline endpoints.
NARRATOR_VERSION = "v2-clean-2025"
Each function takes already-computed data (from diagnose/decide/feedback)
and returns a short human-readable narrative. Falls back to a deterministic
template when the LLM is unavailable.
"""
from __future__ import annotations
import json
from app.intelligence import llm_provider


def available() -> bool:
    return llm_provider.available()


def _ask(system: str, payload: dict, question: str, max_words: int = 140) -> str:
    if not llm_provider.available():
        return ""
    base_prompt = (
        f"DATA (JSON):\n{json.dumps(payload, default=str)[:5000]}\n\n"
        f"TASK: {question}\n"
        f"Answer using only the data above. Max {max_words} words."
    )
    for attempt in range(2):
        try:
            result = llm_provider.complete(
                prompt=base_prompt, system=system, temperature=0.2, max_tokens=350
            )
            text = (result or "").strip()
            if text:
                return text
        except Exception as e:
            # On the last attempt, surface the error
            if attempt == 1:
                return f"(LLM unavailable: {type(e).__name__}: {e})"
        # Small pause before retry — helps with rate limits
        import time; time.sleep(1.5)
    return ""

# ---------------------------------------------------------------- diagnose
DIAGNOSE_SYSTEM = (
    "You are ADAPT's diagnostic engine. Explain ad-campaign anomalies in plain "
    "business language. Cite real campaign IDs, platforms, and numbers. "
    "State the most likely root cause. Be specific, no filler. "
    "Output ONLY the final answer — no preamble, no reasoning, no "
    "'thinking process', no numbered plan, no restating the task."
)


def explain_anomalies(result) -> dict:
    if isinstance(result, dict):
        findings = result.get("findings") or []
        count = result.get("count", len(findings))
    elif isinstance(result, list):
        findings = result
        count = len(findings)
    else:
        findings = []
        count = 0

    if not findings:
        return {
            "narrative": "No anomalies detected in the current dataset.",
            "narrative_source": "template",
            "provider": llm_provider.active_provider(),
        }

    text = _ask(
        DIAGNOSE_SYSTEM,
        {"count": count, "findings": findings[:12]},
        "Summarise these anomalies in plain business language. Group them by "
        "likely root cause, call out the single most urgent one, and state the "
        "estimated revenue at risk when available.",
    )
    if text:
        return {
            "narrative": text,
            "narrative_source": "llm",
            "provider": llm_provider.active_provider(),
        }

    top = findings[0] or {}
    rc = top.get("root_cause", {}) or {}
    name = rc.get("probable_cause") or top.get("type", "anomaly")
    lost = (rc.get("impact", {}) or {}).get("est_lost_revenue")
    tail = f" Est. lost revenue: ${lost:,.0f}." if lost else ""
    return {
        "narrative": f"{count} anomalies detected. "
                     f"Most urgent: {name} ({top.get('severity', 'n/a')}).{tail}",
        "narrative_source": "template",
        "provider": "template",
    }


# ---------------------------------------------------------------- decide
DECIDE_SYSTEM = (
    "You are ADAPT's decision engine. Explain why each recommended action "
    "matters and what outcome it drives. Reference campaign IDs, ROAS, and "
    "budget numbers from the data. No fluff. "
    "Output ONLY the final answer — no preamble, no reasoning, no "
    "'thinking process', no numbered plan, no restating the task."
)

def explain_decisions(actions, opportunities) -> dict:
    if not actions:
        return {
            "narrative": "No actions recommended at this time.",
            "narrative_source": "template",
            "provider": llm_provider.active_provider(),
        }

    text = _ask(
        DECIDE_SYSTEM,
        {"actions": actions[:10], "top_opportunities": opportunities[:8]},
        "Give a 3-5 sentence executive summary: what to do first, why, and "
        "what it's expected to move. Then list the top 3 actions with one line each.",
    )
    if text:
        return {
            "narrative": text,
            "narrative_source": "llm",
            "provider": llm_provider.active_provider(),
        }

    first = actions[0] if isinstance(actions[0], dict) else {}
    return {
        "narrative": f"{len(actions)} actions recommended. Top: "
                     f"{first.get('target', '?')} - {first.get('action', '?')}.",
        "narrative_source": "template",
        "provider": "template",
    }


# ---------------------------------------------------------------- feedback
FEEDBACK_SYSTEM = (
    "You are ADAPT's learning engine. Summarise what worked, what didn't, and "
    "what should change next cycle. Use measured lifts and success rates. "
    "Output ONLY the final answer — no preamble, no reasoning, no "
    "'thinking process', no numbered plan, no restating the task."
)


def explain_feedback(summary) -> dict:
    payload = summary if isinstance(summary, dict) else {"entries": summary}

    text = _ask(
        FEEDBACK_SYSTEM, payload,
        "Summarise the learning: which action types worked, which failed, and "
        "one concrete recommendation for the next cycle.",
    )
    if text:
        return {
            "narrative": text,
            "narrative_source": "llm",
            "provider": llm_provider.active_provider(),
        }

    rate = payload.get("overall_success_rate", "n/a")
    return {
        "narrative": f"Overall success rate: {rate}.",
        "narrative_source": "template",
        "provider": "template",
    }
