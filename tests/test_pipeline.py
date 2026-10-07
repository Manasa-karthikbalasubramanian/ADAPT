"""End-to-end + regression tests. Run: pytest -q"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.data.loader import load_unified
from app.decisions.budget_allocator import allocate, allocate_detail
from app.utils import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    for n in ("executions.jsonl", "actions_log.jsonl"):
        p = store.path(n)
        if p.exists():
            p.unlink()


def findings():
    return client.get("/diagnose/anomalies").json()["findings"]


def of_type(t):
    return [f for f in findings() if f["type"] == t]


# ------------------------------------------------------------- ingestion
def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_unified_join_does_not_multiply_rows():
    raw = pd.read_csv(config.DATA_DIR / "ad_spend.csv")
    ads = load_unified()["ads"]
    assert len(ads) == len(raw)
    assert ads["spend"].sum() == pytest.approx(raw["spend"].sum())
    assert ads["revenue"].sum() == pytest.approx(raw["revenue"].sum())


def test_ingest_endpoints_are_json_safe():
    for url in ("/ingest/raw", "/ingest/unified", "/ingest/summary"):
        assert client.get(url).status_code == 200
    u = client.get("/ingest/unified").json()
    assert "ga_events" in u and "audience" in u


# ------------------------------------------------------------- diagnosis
def test_conversion_crash_is_one_episode_with_funnel_root_cause():
    (crash,) = of_type("conversion_crash")
    assert (crash["date"], crash["end_date"], crash["days"]) == (
        "2025-03-05", "2025-03-07", 3)
    assert crash["weakest_stage"] == "checkout_to_order"
    assert crash["root_cause"]["category"] == "site"


def test_platform_shifts_found_and_decomposed():
    shifts = {f["platform"]: f for f in of_type("platform_shift")}
    assert {"Meta", "TikTok"} <= set(shifts)
    assert all(f["delta_pct"] < -0.3 for f in shifts.values())
    assert shifts["Meta"]["drivers"][0]["driver"] == "cpc"
    assert "CR-107" in shifts["TikTok"]["related"]


def test_stockout_is_single_episode():
    (so,) = of_type("stockout")
    assert so["sku"] == "SKU-1007" and so["days"] == 7 and so["ongoing"]
    assert set(so["campaigns_affected"]) == {"C-003", "C-006", "C-009"}
    assert so["wasted_sessions"] == 9835


def test_creative_fatigue_once_per_creative():
    fat = of_type("creative_fatigue")
    assert [f["creative_id"] for f in fat] == ["CR-107"]


def test_attribution_mismatch_only_where_real():
    att = of_type("attribution_mismatch")
    assert [f["campaign_id"] for f in att] == ["C-004"]
    assert att[0]["reported"] == 441 and att[0]["actual"] == 339


# -------------------------------------------------------------- decisions
def test_opportunities_shape():
    opps = client.get("/decide/opportunities").json()
    assert len(opps) == 15
    assert all(0 <= o["opportunity_score"] <= 1 for o in opps)


def test_actions_respect_guardrails():
    acts = client.get("/decide/actions").json()
    by = {}
    for a in acts:
        by.setdefault(a["action"], set()).add(a["target"])
    # never scale into stockout, fatigue, or a deteriorating platform
    unsafe = {"C-003", "C-006", "C-009", "C-007", "C-004", "C-008"}
    assert not (by["SCALE_BUDGET"] & unsafe)
    assert "C-006" in by["PAUSE_CAMPAIGN"]
    assert {"C-003", "C-009"} <= by["REDUCE_SPEND"]
    assert by["REFRESH_CREATIVE"] == {"CR-107"}
    assert by["HOLD_PLATFORM_SPEND"] == {"Meta", "TikTok"}
    assert "site" in by["INVESTIGATE_SITE"]
    ids = [a["id"] for a in acts]
    assert len(ids) == len(set(ids))                       # no duplicates
    assert all(0 <= a["confidence"] <= 1 for a in acts)


def test_every_proposed_action_is_executable():
    for a in client.get("/decide/actions").json():
        r = client.post("/execute", json={"action": a["action"],
                                          "target": a["target"], "dry_run": True})
        assert r.status_code == 200, (a, r.text)


def test_allocate_sums_to_budget_and_respects_caps():
    alloc = client.get("/decide/allocate?total_budget=25000").json()
    assert sum(alloc.values()) == pytest.approx(25000, abs=0.01)
    assert max(alloc.values()) <= 25000 * config.ALLOC_MAX_PCT + 0.02
    assert not ({"C-006", "C-003", "C-009", "C-007"} & set(alloc))


def test_allocate_rejects_bad_budget():
    assert client.get("/decide/allocate?total_budget=-5").status_code == 422
    assert allocate([], 1000) == {}


def test_allocator_infeasible_cap_equalises():
    opps = [{"campaign_id": f"X{i}", "platform": "P", "roas": 5, "recent_roas": 5,
             "contribution_roas": 3, "stock_headroom": 100, "oos_exposure": 0,
             "creative_fatigue": 0, "opportunity_score": 0.5 + i / 10,
             "daily_spend": 10} for i in range(3)]
    alloc = allocate(opps, 900, max_pct=0.1)       # 3 x 10% < 100% -> relax
    assert sum(alloc.values()) == pytest.approx(900)
    assert len(allocate_detail(opps, 900)) == 3


def test_brief():
    b = client.get("/decide/brief").json()
    assert b["narrative"] and b["actions"] and b["diagnosis"]


# -------------------------------------------------------------- execution
PAYLOAD = {"action": "SCALE_BUDGET", "target": "C-001",
           "params": {"increase_pct": 20}}


def test_execute_idempotent_per_params():
    a = client.post("/execute", json=PAYLOAD).json()
    b = client.post("/execute", json=PAYLOAD).json()
    c = client.post("/execute", json={**PAYLOAD, "params": {"increase_pct": 10}}).json()
    assert a["idempotent"] is False and b["idempotent"] is True and a["id"] == b["id"]
    assert c["idempotent"] is False and c["id"] != a["id"]
    assert len(client.get("/execute/log").json()) == 2


def test_dry_run_does_not_persist_or_collide():
    d = client.post("/execute", json={**PAYLOAD, "dry_run": True}).json()
    assert d["status"] == "dry_run"
    assert client.get("/execute/log").json() == []
    real = client.post("/execute", json=PAYLOAD).json()
    assert real["status"] == "simulated" and real["idempotent"] is False


@pytest.mark.parametrize("payload", [
    {"action": "NOPE", "target": "C-001"},
    {"action": "PAUSE_CAMPAIGN", "target": "C-999"},
    {"action": "HOLD_PLATFORM_SPEND", "target": "C-001"},       # wrong target kind
    {"action": "SCALE_BUDGET", "target": "C-001", "params": {"increase_pct": 500}},
    {"action": "SCALE_BUDGET", "target": "C-001", "params": {"increase_pct": "x"}},
    {"action": "SCALE_BUDGET", "target": "C-001", "params": {"effective_date": "nope"}},
])
def test_execute_validation(payload):
    r = client.post("/execute", json=payload)
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_execution_platform_comes_from_data():
    r = client.post("/execute", json={"action": "PAUSE_CAMPAIGN", "target": "C-008"}).json()
    assert r["platform_response"]["platform"] == "TikTok"


def test_unknown_ids_404():
    assert client.get("/execute/zzz").status_code == 404
    assert client.post("/feedback/outcome",
                       json={"action_id": "zzz", "outcome": {}}).status_code == 404


def test_query_param_bounds():
    assert client.get("/execute/log?limit=-1").status_code == 422
    assert client.get("/execute/log?limit=100000").status_code == 422


# ------------------------------------------------------- impact + learning
def test_impact_pending_when_no_future_data():
    ex = client.post("/execute", json=PAYLOAD).json()
    imp = client.get(f"/execute/{ex['id']}/impact").json()["impact"]
    assert imp["verdict"] == "pending_data"
    assert imp["projection"]["delta_spend_per_day"] > 0


def test_impact_with_effective_date_in_data():
    ex = client.post("/execute", json={
        "action": "HOLD_PLATFORM_SPEND", "target": "Meta",
        "params": {"effective_date": "2025-03-07"}}).json()
    imp = client.get(f"/execute/{ex['id']}/impact?window_days=3").json()["impact"]
    assert imp["found"] and imp["before"]["days"] == 3 and imp["after"]["days"] == 3
    assert imp["verdict"] in {"improved", "worsened", "neutral"}


def test_feedback_roundtrip_and_learning_loop():
    log = client.post("/feedback/log",
                      json={"action": {"action": "TEST"}, "actor": "pytest"}).json()
    out = client.post("/feedback/outcome",
                      json={"action_id": log["id"], "outcome": {"success": True}}).json()
    assert out["updated"] is True

    ex = client.post("/execute", json={
        "action": "HOLD_PLATFORM_SPEND", "target": "Meta",
        "params": {"effective_date": "2025-03-07"}}).json()
    ev = client.post(f"/feedback/evaluate/{ex['id']}").json()
    assert ev["learned"]["updated"] is True
    assert "HOLD_PLATFORM_SPEND" in client.get("/feedback/summary").json()


def test_learning_changes_step_size():
    from app.decisions.engine import decide
    from app.intelligence.anomalies import detect_anomalies
    from app.intelligence.opportunity import score_opportunities
    u = load_unified()
    d, o = detect_anomalies(u), score_opportunities(u)
    base = next(a for a in decide(d, o, learned={}) if a["action"] == "SCALE_BUDGET")
    shrunk = next(a for a in decide(d, o, learned={"SCALE_BUDGET": {
        "n": 5, "success_rate": 0.2, "step_multiplier": 0.7}})
        if a["id"] == base["id"])
    assert shrunk["suggested_increase_pct"] < base["suggested_increase_pct"]
    assert "learning_note" in shrunk


def test_pending_impact_is_not_learned_from():
    ex = client.post("/execute", json=PAYLOAD).json()
    ev = client.post(f"/feedback/evaluate/{ex['id']}").json()
    assert ev["impact"]["verdict"] == "pending_data" and ev["learned"] is None


# --------------------------------------------------------------- analytics
@pytest.mark.parametrize("url", ["/analytics/daily", "/analytics/by-platform",
                                 "/analytics/by-campaign", "/analytics/funnel",
                                 "/analytics/timeline"])
def test_analytics(url):
    r = client.get(url)
    assert r.status_code == 200 and isinstance(r.json(), list) and r.json()


def test_timeline_marks_incident_days():
    tl = {d["date"]: d for d in client.get("/analytics/timeline").json()}
    assert any(e["type"] == "conversion_crash" for e in tl["2025-03-06"]["events"])
    assert not any(e["type"] == "conversion_crash" for e in tl["2025-03-02"]["events"])
    assert any(e["type"] == "stockout" for e in tl["2025-03-09"]["events"])
