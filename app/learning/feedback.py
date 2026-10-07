"""Closed-loop learning: log actions, record outcomes, learn step-size policy.

Outcomes (manual, or auto-derived from measured impact) feed
`learned_adjustments()`, which the decision engine uses to scale how
aggressive each action type is and to temper its confidence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.utils import store
from app.utils.errors import NotFound

LOG = "actions_log.jsonl"
MIN_SAMPLES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_action(action: dict, actor: str = "system", rec_id: str | None = None) -> dict:
    rec = {"id": rec_id or str(uuid.uuid4()), "ts": _now(), "actor": actor,
           "action": action, "outcome": None}
    store.append(LOG, rec)
    return rec


def read_log() -> list[dict]:
    return store.read_all(LOG)


def record_outcome(action_id: str, outcome: dict) -> dict:
    with store.lock():
        entries = read_log()
        hit = [e for e in entries if e["id"] == action_id]
        if not hit:
            raise NotFound(f"No logged action with id {action_id}")
        for e in hit:
            e["outcome"] = {**outcome, "recorded_at": _now()}
        store.rewrite(LOG, entries)
    return {"updated": True, "action_id": action_id, "outcome": hit[0]["outcome"]}


def record_execution_outcome(execution: dict, impact: dict) -> dict:
    """Turn a measured impact into a labelled outcome for learning."""
    with store.lock():
        if not any(e["id"] == execution["id"] for e in read_log()):
            log_action({"action": execution["action"], "target": execution["target"],
                        "params": execution.get("params", {}),
                        "execution_id": execution["id"]},
                       actor=execution.get("actor", "system"),
                       rec_id=execution["id"])
        return record_outcome(execution["id"], {
            "success": impact.get("verdict") == "improved",
            "verdict": impact.get("verdict"),
            "delta_pct": impact.get("delta_pct"),
            "source": "measured_impact",
        })


def summary() -> dict:
    """Per action-type success statistics and the step multiplier derived."""
    stats: dict[str, dict] = {}
    for e in read_log():
        o = e.get("outcome")
        if not o or o.get("success") is None:
            continue
        a = e["action"].get("action")
        if not a:
            continue
        s = stats.setdefault(a, {"n": 0, "wins": 0})
        s["n"] += 1
        s["wins"] += 1 if o["success"] else 0
    for a, s in stats.items():
        rate = (s["wins"] + 1) / (s["n"] + 2)             # Laplace smoothing
        s["success_rate"] = round(rate, 3)
        s["step_multiplier"] = (round(min(max(0.5 + rate, 0.5), 1.25), 2)
                                if s["n"] >= MIN_SAMPLES else 1.0)
    return stats


def learned_adjustments() -> dict[str, dict]:
    return summary()
