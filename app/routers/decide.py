from fastapi import APIRouter, Query

from app.data.loader import load_unified
from app.intelligence.opportunity import score_opportunities
from app.decisions.engine import decide
from app.decisions.budget_allocator import allocate, allocate_detail
from app import config
from app.intelligence import narrator
from app.intelligence.anomalies import detect_anomalies

router = APIRouter(prefix="/decide", tags=["decide"])


def _findings(unified) -> list:
    """detect_anomalies returns {"count": N, "findings": [...]}.
    engine.decide() wants the bare findings list."""
    diag = detect_anomalies(unified)
    return diag.get("findings", []) if isinstance(diag, dict) else diag


@router.get("/opportunities")
def opportunities():
    return score_opportunities(load_unified())


@router.get("/actions")
def actions():
    unified = load_unified()
    opps = score_opportunities(unified)
    acts = decide(_findings(unified), opps)

    explain = narrator.explain_decisions(acts, opps)
    return {
        "actions": acts,
        "opportunities": opps,
        "narrative": explain["narrative"],
        "narrative_source": explain["narrative_source"],
    }


@router.get("/allocate")
def allocate_budget(
    total_budget: float = Query(10000.0, gt=0),
    max_pct: float = Query(config.ALLOC_MAX_PCT, gt=0, le=1),
):
    return allocate(
        score_opportunities(load_unified()), total_budget, max_pct=max_pct
    )


@router.get("/allocate/detail")
def allocate_budget_detail(
    total_budget: float = Query(10000.0, gt=0),
    max_pct: float = Query(config.ALLOC_MAX_PCT, gt=0, le=1),
):
    return allocate_detail(
        score_opportunities(load_unified()), total_budget, max_pct=max_pct
    )


@router.get("/actions")
def actions():
    unified = load_unified()
    opps = score_opportunities(unified)
    acts = decide(_findings(unified), opps)

    explain = narrator.explain_decisions(acts, opps)
    return {
        "actions": acts,
        "opportunities": opps,
        "narrative": explain["narrative"],
        "narrative_source": explain["narrative_source"],
    }



@router.get("/brief")
def brief():
    unified = load_unified()
    opps = score_opportunities(unified)
    acts = decide(_findings(unified), opps)

    explain = narrator.explain_decisions(acts, opps)
    return {
        "opportunities": opps,
        "actions": acts,
        "narrative": explain["narrative"],
        "narrative_source": explain["narrative_source"],
    }