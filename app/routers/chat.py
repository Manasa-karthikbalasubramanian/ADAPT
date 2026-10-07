from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel, Field

from app.intelligence import chatbot, llm_provider

router = APIRouter(prefix="/chat", tags=["chat"])

STATIC = Path(__file__).resolve().parent.parent / "static"


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list)


@router.post("")
def chat(payload: ChatIn):
    return chatbot.answer(payload.message, payload.history)


@router.get("/status")
def status():
    return llm_provider.status()


@router.get("/snapshot")
def snap():
    return chatbot.snapshot()


@router.get("/ui", response_class=HTMLResponse)
def ui():
    return (STATIC / "chat.html").read_text(encoding="utf-8")
