from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from app.execution.executor import execute, list_executions, get_execution, SUPPORTED, TARGET_KIND
from app.execution.impact import measure

router = APIRouter(prefix="/execute", tags=["execute"])


class ExecuteIn(BaseModel):
    action: str = Field(..., description="e.g. SCALE_BUDGET, PAUSE_CAMPAIGN")
    target: str = Field(..., description="campaign_id, platform, creative_id or 'site'")
    params: dict = Field(default_factory=dict,
                         description="increase_pct / decrease_pct (0-100], "
                                     "effective_date (YYYY-MM-DD)")
    actor: str = "system"
    dry_run: bool = False


@router.get("/supported")
def supported():
    return {"actions": sorted(SUPPORTED), "target_kind": TARGET_KIND}


@router.post("")
def do_execute(payload: ExecuteIn):
    return execute(payload.action, payload.target, payload.params,
                   payload.actor, payload.dry_run)


@router.get("/log")
def log(limit: int = Query(50, ge=1, le=500)):
    return list_executions(limit)


@router.get("/{exec_id}")
def get_one(exec_id: str):
    return get_execution(exec_id)


@router.get("/{exec_id}/impact")
def impact(exec_id: str, window_days: int = Query(3, ge=1, le=30)):
    execution = get_execution(exec_id)
    return {"execution": execution, "impact": measure(execution, window_days)}
