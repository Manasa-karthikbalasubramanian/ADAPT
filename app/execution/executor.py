"""Executes approved decisions (simulated ad-platform calls).

Safety properties:
* targets are validated against real campaigns / platforms / creatives
* percentage params are range-checked
* idempotent per (action, target, params) per UTC day -> double-clicks are safe
* dry runs are never persisted and never collide with real executions
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from app.data.loader import load_unified
from app.utils import store
from app.utils.errors import BadRequest, NotFound

log = logging.getLogger("executor")
EXEC_LOG = "executions.jsonl"

# action -> kind of target it operates on
TARGET_KIND = {
    "SCALE_BUDGET": "campaign",
    "CUT_BUDGET": "campaign",
    "REDUCE_SPEND": "campaign",
    "PAUSE_CAMPAIGN": "campaign",
    "INVESTIGATE_ATTRIBUTION": "campaign",
    "HOLD_PLATFORM_SPEND": "platform",
    "REFRESH_CREATIVE": "creative",
    "INVESTIGATE_SITE": "site",
}
SUPPORTED = set(TARGET_KIND)
PCT_PARAM = {"SCALE_BUDGET": "increase_pct", "CUT_BUDGET": "decrease_pct",
             "REDUCE_SPEND": "decrease_pct"}
DEFAULT_PCT = {"SCALE_BUDGET": 20, "CUT_BUDGET": 30, "REDUCE_SPEND": 20}


def _valid_targets() -> dict[str, set[str]]:
    ads = load_unified()["ads"]
    return {"campaign": set(ads["campaign_id"]), "platform": set(ads["platform"]),
            "creative": set(ads["creative_id"]), "site": {"site"}}


def _platform_of(target: str, kind: str) -> str:
    ads = load_unified()["ads"]
    if kind == "campaign":
        return str(ads.loc[ads["campaign_id"] == target, "platform"].iloc[0])
    if kind == "creative":
        return str(ads.loc[ads["creative_id"] == target, "platform"].iloc[0])
    return target if kind == "platform" else "internal"


def _validate(action: str, target: str, params: dict) -> str:
    if action not in SUPPORTED:
        raise BadRequest(f"Unsupported action '{action}'",
                         {"supported": sorted(SUPPORTED)})
    kind = TARGET_KIND[action]
    valid = _valid_targets()[kind]
    if target not in valid:
        raise BadRequest(f"Unknown {kind} '{target}' for {action}",
                         {"valid_targets": sorted(valid)})
    key = PCT_PARAM.get(action)
    if key and key in params:
        v = params[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 100:
            raise BadRequest(f"params.{key} must be a number in (0, 100]")
    if "effective_date" in params:
        try:
            datetime.strptime(str(params["effective_date"]), "%Y-%m-%d")
        except ValueError:
            raise BadRequest("params.effective_date must be YYYY-MM-DD")
    return kind


def _fingerprint(action: str, target: str, params: dict) -> str:
    blob = json.dumps({"a": action, "t": target, "p": params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def already_executed(fingerprint: str) -> dict | None:
    today = datetime.now(timezone.utc).date().isoformat()
    for r in store.read_all(EXEC_LOG):
        if r.get("fingerprint") == fingerprint and r["ts"].startswith(today):
            return r
    return None


def execute(action: str, target: str, params: dict | None = None,
            actor: str = "system", dry_run: bool = False) -> dict:
    params = params or {}
    kind = _validate(action, target, params)
    fp = _fingerprint(action, target, params)

    with store.lock():
        if not dry_run:
            existing = already_executed(fp)
            if existing:
                log.info("idempotent hit", extra={"extra_fields": {
                    "action": action, "target": target, "exec_id": existing["id"]}})
                return {**existing, "idempotent": True}

        receipt = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "target": target,
            "params": params,
            "actor": actor,
            "fingerprint": fp,
            "status": "dry_run" if dry_run else "simulated",
            "platform_response": _simulate_platform_call(action, target, params, kind),
        }
        if not dry_run:
            store.append(EXEC_LOG, receipt)

    log.info("executed", extra={"extra_fields": {
        "exec_id": receipt["id"], "action": action, "target": target,
        "dry_run": dry_run}})
    return {**receipt, "idempotent": False}


def _simulate_platform_call(action: str, target: str, params: dict, kind: str) -> dict:
    plat = _platform_of(target, kind)
    if action in PCT_PARAM:
        pct = params.get(PCT_PARAM[action], DEFAULT_PCT[action])
        sign = 1 if action == "SCALE_BUDGET" else -1
        return {"platform": plat, "op": "budget_update", "target": target,
                "delta_pct": sign * pct, "accepted": True}
    if action == "PAUSE_CAMPAIGN":
        return {"platform": plat, "op": "campaign_status", "target": target,
                "status": "PAUSED", "accepted": True}
    if action == "HOLD_PLATFORM_SPEND":
        return {"platform": plat, "op": "spend_hold", "target": target,
                "status": "HOLD", "accepted": True}
    if action == "REFRESH_CREATIVE":
        return {"platform": plat, "op": "rotation_request", "target": target,
                "queued": True}
    if action == "INVESTIGATE_ATTRIBUTION":
        return {"platform": "internal", "op": "ticket_created", "target": target,
                "ticket": f"ATTR-{target}"}
    if action == "INVESTIGATE_SITE":
        return {"platform": "internal", "op": "ticket_created", "target": target,
                "ticket": "SITE-CVR-INCIDENT"}
    return {"accepted": False}


def list_executions(limit: int = 50) -> list[dict]:
    return store.read_all(EXEC_LOG)[-limit:][::-1]


def get_execution(exec_id: str) -> dict:
    for r in store.read_all(EXEC_LOG):
        if r["id"] == exec_id:
            return r
    raise NotFound(f"No execution with id {exec_id}")
