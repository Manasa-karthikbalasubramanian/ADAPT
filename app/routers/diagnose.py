"""Diagnose router — anomaly detection with LLM narrative."""
from fastapi import APIRouter

from app.data.loader import load_unified
from app.intelligence.anomalies import detect_anomalies
from app.intelligence import narrator

router = APIRouter(prefix="/diagnose", tags=["diagnose"])


@router.get("/anomalies")
def anomalies():
    unified = load_unified()
    result = detect_anomalies(unified)          # whole result (dict OR list)
    explain = narrator.explain_anomalies(result)

    # Preserve whatever shape detect_anomalies returned
    if isinstance(result, dict):
        body = dict(result)
    else:
        body = {"findings": result, "count": len(result)}

    body["narrative"] = explain["narrative"]
    body["narrative_source"] = explain["narrative_source"]
    return body