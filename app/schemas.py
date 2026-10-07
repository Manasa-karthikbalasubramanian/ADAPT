"""Pydantic request/response schemas."""
from pydantic import BaseModel
from typing import Optional


class ActionIn(BaseModel):
    action: dict
    actor: str = "system"


class OutcomeIn(BaseModel):
    action_id: str
    outcome: dict


class ActionOut(BaseModel):
    id: str
    ts: str
    actor: str
    action: dict
    outcome: Optional[dict] = None


class OpportunityOut(BaseModel):
    campaign_id: str
    platform: str
    roas: float
    weighted_margin_pct: float
    cvr_trend: float
    stock_headroom: int
    attribution_trust: float
    opportunity_score: float


class DecisionOut(BaseModel):
    action: str
    target: str
    rationale: str
    priority: str
    suggested_increase_pct: Optional[int] = None
    suggested_decrease_pct: Optional[int] = None
