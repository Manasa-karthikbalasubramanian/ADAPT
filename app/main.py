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


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}

