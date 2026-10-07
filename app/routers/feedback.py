from fastapi import APIRouter, Query

from app.schemas import ActionIn, OutcomeIn
from app.learning import feedback as fb
from app.execution.executor import get_execution
from app.execution.impact import measure
from app.intelligence import narrator

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("/log")
def log(payload: ActionIn):
    return fb.log_action(payload.action, payload.actor)


@router.get("/log")
def list_log():
    return fb.read_log()


@router.post("/outcome")
def outcome(payload: OutcomeIn):
    return fb.record_outcome(payload.action_id, payload.outcome)


@router.post("/evaluate/{exec_id}")
def evaluate(exec_id: str, window_days: int = Query(3, ge=1, le=30)):
    """Measure an execution's impact and feed the verdict back into learning.
    A `pending_data` verdict is returned but NOT learned from."""
    execution = get_execution(exec_id)
    impact = measure(execution, window_days)
    learned = None
    if impact.get("verdict") in ("improved", "worsened", "neutral"):
        learned = fb.record_execution_outcome(execution, impact)
    return {"execution": execution, "impact": impact, "learned": learned}


@router.get("/summary")
def summary():
    data = fb.summary()
    if not isinstance(data, dict):
        data = {"entries": data}
    explain = narrator.explain_feedback(data)
    return {
        **data,
        "narrative": explain["narrative"],
        "narrative_source": explain["narrative_source"],
    }