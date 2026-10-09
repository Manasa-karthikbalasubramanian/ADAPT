"""D2C Advertising Intelligence API - FastAPI entry point."""
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


from app.routers import (ingest, diagnose, decide, feedback,
                         execute, analytics, chat)
from app.utils import errors
from app.utils.logging import setup as setup_logging

setup_logging()

app = FastAPI(
    title="D2C Advertising Intelligence API",
    version="1.1.0",
    description=(
        "Autonomous decision engine for DataQuest 3.0. "
        "Ingest -> Diagnose -> Decide -> Execute -> Learn. "
        "Includes ADAPT conversational assistant."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

errors.install(app)

STATIC = Path(__file__).resolve().parent / "static"
STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

app.include_router(ingest.router)
app.include_router(diagnose.router)
app.include_router(decide.router)
app.include_router(execute.router)
app.include_router(feedback.router)
app.include_router(analytics.router)
app.include_router(chat.router)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/dashboard")
def dashboard():
    return FileResponse("app/static/dashboard.html")

@app.get("/", tags=["meta"])
def root():
    return {
        "status": "ok",
        "service": "d2c-ad-intel",
        "version": app.version,
        "docs": "/docs",
        "chat_ui": "/chat/ui",
    }


@app.get("/health")
def health():
    return {"status": "ok", "ok": True}

# ══════════════════════════════════════════════════════════════════════
#  ADAPT DASHBOARD ADAPTER ROUTES
#  Bridges dashboard.html expectations to the existing backend API.
#  Internal HTTP calls to 127.0.0.1:8000 — pragmatic, works immediately.
# ══════════════════════════════════════════════════════════════════════

import requests
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional

_INTERNAL = "http://127.0.0.1:8000"

def _get(path: str, **params):
    try:
        r = requests.get(f"{_INTERNAL}{path}", params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def _post(path: str, payload: dict):
    try:
        r = requests.post(f"{_INTERNAL}{path}", json=payload, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

# ── Static mount (idempotent) ──
try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except Exception:
    pass  # already mounted

@app.get("/dashboard")
def dashboard():
    return FileResponse("app/static/dashboard.html")

# ── /diagnose/full — chain everything into one response ──
@app.get("/diagnose/full")
def diagnose_full():
    diag = _get("/diagnose/anomalies") or {}
    opps_raw = _get("/decide/opportunities") or {}
    acts_raw = _get("/decide/actions") or {}

    # Normalize anomalies
    anomalies = diag.get("findings") or diag.get("anomalies") or []
    if isinstance(anomalies, dict):
        anomalies = anomalies.get("findings", [])

    # Normalize opportunities
    opps = opps_raw.get("opportunities") if isinstance(opps_raw, dict) else opps_raw
    opps = opps or []

    # Normalize recommendations
    recs = acts_raw.get("actions") if isinstance(acts_raw, dict) else acts_raw
    recs = recs or []
    for i, r in enumerate(recs):
        r.setdefault("id", i + 1)
        r.setdefault("status", "pending")
        # Synthesize confidence if backend didn't provide one
        if "confidence" not in r:
            score = r.get("score") or r.get("opportunity_score") or 0.7
            r["confidence"] = round(float(score), 2)

    # Build narrative
    narrative = diag.get("narrative") or diag.get("summary") or ""
    if not narrative and anomalies:
        first = anomalies[0]
        narrative = (
            f"{first.get('entity_id','—')} shows a {abs(first.get('delta_pct',0)):.0f}% "
            f"shift in {first.get('metric','roas').upper()} vs baseline. "
            f"{len(anomalies)} anomalies detected across the portfolio."
        )

    return {
        "anomalies": anomalies,
        "drivers": diag.get("drivers", []) or [],
        "narrative": narrative,
        "recommendations": recs,
        "top_opportunities": opps,
    }

# ── /simulate/loop — identical to /diagnose/full for now ──
@app.post("/simulate/loop")
def simulate_loop():
    return diagnose_full()

# ── /execute/recommendations — actions from the decide layer ──
@app.get("/execute/recommendations")
def execute_recommendations():
    raw = _get("/decide/actions") or {}
    recs = raw.get("actions") if isinstance(raw, dict) else raw
    recs = recs or []
    for i, r in enumerate(recs):
        r.setdefault("id", i + 1)
        r.setdefault("status", "pending")
        if "confidence" not in r:
            score = r.get("score") or r.get("opportunity_score") or 0.7
            r["confidence"] = round(float(score), 2)
    return recs

# ── /execute/run — adapt frontend payload to backend /execute ──
@app.post("/execute/run")
def execute_run(payload: dict):
    rec_id = payload.get("recommendation_id")
    dry = payload.get("dry_run", True)

    # Look up the recommendation by re-fetching actions
    actions = execute_recommendations()
    rec = next((r for r in actions if r.get("id") == rec_id), None)
    if not rec:
        return {"success": False, "message": f"Recommendation {rec_id} not found",
                "response": {"error": "not_found"}}

    # Adapt to backend /execute signature
    backend_payload = {
        "action":   rec.get("action", "reallocate"),
        "target":   rec.get("to_entity") or rec.get("from_entity") or "",
        "params":   {
            "delta_budget": rec.get("delta_budget", 0),
            "from_entity":  rec.get("from_entity"),
            "to_entity":    rec.get("to_entity"),
        },
        "actor":    "dashboard",
        "dry_run":  dry,
    }
    res = _post("/execute", backend_payload)
    if res is None:
        return {"success": False, "message": "Execution failed",
                "response": {"error": "backend_error"}}

    return {
        "success": True,
        "platform": rec.get("platform") or rec.get("to_entity", "").split("|")[0] or "meta",
        "request": backend_payload,
        "response": res,
        "message": "simulated" if dry else "executed",
    }

# ── /feed/events — execution log reshaped ──
@app.get("/feed/events")
def feed_events(limit: int = 30):
    raw = _get("/execute/log", limit=limit) or {}
    events = raw.get("executions") if isinstance(raw, dict) else raw
    events = events or []

    # Reshape into the {kind, entity_id, severity, ts} format frontend expects
    out = []
    for e in events:
        out.append({
            "kind": e.get("action") or e.get("kind") or "execute",
            "entity_id": e.get("target") or e.get("entity_id") or "",
            "severity": "info",
            "ts": e.get("created_at") or e.get("ts") or "",
        })
    return {"events": out}

# ── /feed/feedback — adapt to /feedback/evaluate ──
@app.post("/feed/feedback")
def feed_feedback(payload: dict):
    exec_id = payload.get("execution_id")
    pre = float(payload.get("pre_value", 0) or 0)
    post = float(payload.get("post_value", 0) or 0)
    uplift = ((post - pre) / pre * 100) if pre else 0

    backend_payload = {
        "metric": "roas",
        "pre_value": pre,
        "post_value": post,
        "window_hours": payload.get("window_hours", 24),
        "notes": payload.get("notes", "logged from dashboard"),
    }
    _post(f"/feedback/evaluate/{exec_id}", backend_payload) if exec_id else None
    return {**payload, "uplift": uplift}

# ── /guardrails — expose the actual thresholds as a policy panel ──
@app.get("/guardrails")
def guardrails():
    try:
        from app import config as cfg
        return {
            "policies": [
                {"label": "Scale score minimum",  "value": getattr(cfg, "SCALE_SCORE_MIN", 0.65),      "unit": ""},
                {"label": "ROAS scale threshold", "value": getattr(cfg, "ROAS_SCALE_THRESHOLD", 8.0),   "unit": "×"},
                {"label": "ROAS pause threshold", "value": getattr(cfg, "ROAS_PAUSE_THRESHOLD", 2.0),   "unit": "×"},
                {"label": "Margin floor",         "value": getattr(cfg, "MARGIN_MIN", 0.30),           "unit": ""},
                {"label": "Stock headroom min",   "value": getattr(cfg, "STOCK_HEADROOM_MIN", 50),     "unit": "units"},
                {"label": "Stock cover minimum",  "value": getattr(cfg, "STOCK_COVER_MIN_DAYS", 3.0),  "unit": "days"},
                {"label": "Allocation cap",       "value": getattr(cfg, "ALLOC_MAX_PCT", 0.25),        "unit": ""},
            ]
        }
    except Exception:
        return {"policies": []}
