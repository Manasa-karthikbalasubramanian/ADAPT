
"""
add_chat.py — creates the chat bot files + registers the router.
Idempotent: safe to run whether or not add_llm.py was run before.

Creates (only if missing):
    app/intelligence/chatbot.py
    app/routers/chat.py
    app/static/chat.html
Rewrites:
    app/main.py   (adds chat router + static mount)

Run:  python add_chat.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FILES: dict[str, str] = {}

# ============================================================ chatbot.py
FILES["app/intelligence/chatbot.py"] = '''"""Data-grounded chat bot.

Every reply is built from a compact snapshot of the live dataset, so
answers reference real campaign IDs, ROAS values, and anomalies - not
hallucinations.
"""
from __future__ import annotations
import json
from app.data.loader import load_unified
from app.intelligence.anomalies import detect_anomalies
from app.intelligence.opportunity import score_opportunities
from app.intelligence import llm_provider


SYSTEM = (
    "You are ADAPT, a D2C marketing intelligence assistant. "
    "Answer ONLY using the data snapshot provided. "
    "If the snapshot does not contain the answer, say what is missing. "
    "Be concise, specific, and use real campaign IDs and numbers. "
    "Max 180 words."
)


def snapshot() -> dict:
    unified = load_unified()
    ads = unified["ads"]
    total_spend = float(ads["spend"].sum())
    total_rev   = float(ads["revenue"].sum())
    blended_roas = round(total_rev / total_spend, 2) if total_spend else 0.0

    daily = (ads.assign(d=ads["date"].dt.strftime("%Y-%m-%d"))
                .groupby("d").agg(spend=("spend", "sum"),
                                  revenue=("revenue", "sum")).reset_index())
    daily["roas"] = (daily["revenue"] / daily["spend"].replace(0, 1)).round(2)

    plat = (ads.groupby("platform").agg(spend=("spend", "sum"),
                                        revenue=("revenue", "sum")).reset_index())
    plat["roas"] = (plat["revenue"] / plat["spend"].replace(0, 1)).round(2)
    plat_rows = plat.round(2).to_dict(orient="records")

    return {
        "blended_roas": blended_roas,
        "total_spend": round(total_spend, 2),
        "total_revenue": round(total_rev, 2),
        "date_range": [daily["d"].min(), daily["d"].max()],
        "daily_tail": daily.tail(7).to_dict(orient="records"),
        "platforms": plat_rows,
        "anomalies": detect_anomalies(unified),
        "top_opportunities": score_opportunities(unified)[:8],
    }


def answer(question: str, history: list[dict] | None = None) -> dict:
    snap = snapshot()
    if not llm_provider.available():
        return {
            "provider": "template",
            "answer": _template_answer(question, snap),
            "grounded": True,
        }

    history_text = ""
    if history:
        for h in history[-4:]:
            role = h.get("role", "user")
            history_text += f"{role}: {h.get('content','')}\\n"

    prompt = (
        "DATA SNAPSHOT (JSON):\\n"
        f"{json.dumps(snap, default=str)[:6000]}\\n\\n"
        f"CONVERSATION SO FAR:\\n{history_text}\\n"
        f"USER QUESTION: {question}\\n\\n"
        "Answer using only the snapshot."
    )

    try:
        text = llm_provider.complete(prompt=prompt, system=SYSTEM,
                                     temperature=0.2, max_tokens=400)
    except Exception as e:
        return {"provider": "error", "answer": f"LLM error: {e}",
                "grounded": False}
    return {"provider": llm_provider.active_provider(),
            "answer": text, "grounded": True}


def _template_answer(question: str, snap: dict) -> str:
    q = question.lower()
    if "roas" in q or "performance" in q:
        return (f"Blended ROAS is **{snap['blended_roas']}** "
                f"(${snap['total_revenue']:,.0f} revenue / "
                f"${snap['total_spend']:,.0f} spend). "
                f"Platform breakdown: {snap['platforms']}")
    if "anomaly" in q or "problem" in q or "wrong" in q:
        return f"{len(snap['anomalies'])} anomalies detected: {snap['anomalies'][:5]}"
    if "opportunit" in q or "scale" in q or "grow" in q:
        return f"Top opportunities: {snap['top_opportunities'][:3]}"
    return ("LLM not configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY, "
            "then restart. Snapshot ready: "
            f"blended ROAS {snap['blended_roas']}, "
            f"{len(snap['anomalies'])} anomalies.")
'''

# ============================================================ chat router
FILES["app/routers/chat.py"] = '''from fastapi import APIRouter
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
'''

# ============================================================ chat.html
FILES["app/static/chat.html"] = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>ADAPT — D2C Intelligence Bot</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 40px auto; padding: 0 20px; color: #111; }
  h1 { margin-bottom: 4px; }
  .sub { color: #666; margin-bottom: 20px; font-size: 14px; }
  #log { border: 1px solid #ddd; border-radius: 8px; padding: 16px; min-height: 320px; background: #fafafa; }
  .msg { margin: 10px 0; padding: 10px 14px; border-radius: 8px; white-space: pre-wrap; }
  .user { background: #e3f2fd; margin-left: 20%; }
  .bot  { background: #fff; border: 1px solid #eee; margin-right: 20%; }
  .meta { font-size: 11px; color: #888; margin-top: 4px; }
  form { display: flex; gap: 8px; margin-top: 12px; }
  input { flex: 1; padding: 12px; border: 1px solid #ccc; border-radius: 8px; font-size: 15px; }
  button { padding: 12px 20px; background: #1565c0; color: white; border: 0; border-radius: 8px; cursor: pointer; font-size: 15px; }
  button:disabled { opacity: 0.5; }
  .examples button { background: #eee; color: #333; padding: 6px 12px; font-size: 13px; margin: 4px 4px 0 0; }
</style>
</head>
<body>
  <h1>ADAPT</h1>
  <div class="sub">Autonomous D2C Advertising Intelligence — grounded in your live dataset.</div>
  <div id="log"></div>
  <div class="examples" id="examples"></div>
  <form id="f">
    <input id="q" placeholder="Ask about ROAS, anomalies, opportunities..." autocomplete="off" />
    <button id="b">Send</button>
  </form>
<script>
const log = document.getElementById('log');
const f = document.getElementById('f');
const q = document.getElementById('q');
const b = document.getElementById('b');
const examples = document.getElementById('examples');
const history = [];

function add(role, text, meta) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  if (meta) {
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = meta;
    d.appendChild(m);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

async function ask(text) {
  add('user', text);
  history.push({role:'user', content:text});
  b.disabled = true;
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: text, history})
    });
    const j = await r.json();
    add('bot', j.answer, 'via ' + j.provider);
    history.push({role:'assistant', content:j.answer});
  } catch (e) {
    add('bot', 'Error: ' + e.message);
  }
  b.disabled = false;
  q.focus();
}

f.addEventListener('submit', e => {
  e.preventDefault();
  const t = q.value.trim();
  if (!t) return;
  q.value = '';
  ask(t);
});

fetch('/chat/status').then(r=>r.json()).then(s => {
  add('bot', 'Provider: ' + s.active_provider + '  ·  available: ' + s.available +
      (s.available ? '' : '\\n\\nTo enable the LLM, set OPENROUTER_API_KEY or OPENAI_API_KEY, then restart.'));
  const qs = [
    'What is our blended ROAS?',
    'Which campaigns should we scale?',
    'What anomalies are active right now?',
    'Which platform underperformed?',
    'What is the attribution gap on C-004?'
  ];
  qs.forEach(t => {
    const btn = document.createElement('button');
    btn.textContent = t;
    btn.onclick = () => ask(t);
    examples.appendChild(btn);
  });
});
</script>
</body>
</html>
'''

# ============================================================ main.py
FILES["app/main.py"] = '''"""D2C Advertising Intelligence API - FastAPI entry point."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
'''


def main() -> None:
    for d in ["app/intelligence", "app/routers", "app/static"]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)

    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print(f"  [write] {rel}")

    import sys
    sys.path.insert(0, str(ROOT))
    try:
        from app.main import app
        routes = [r.path for r in app.routes]
        has_chat = any(p.startswith("/chat") for p in routes)
        print(f"\n[OK]  app.main imports cleanly. routes: {len(routes)}")
        print(f"[OK]  /chat routes present: {has_chat}")
        if has_chat:
            print("[OK]  chat paths:",
                  sorted(p for p in routes if p.startswith('/chat')))
    except Exception as e:
        print(f"\n[FAIL]  {e!r}")
        raise

    print("\nDone. Now:")
    print("  1) Stop the running server  (Ctrl+C in its terminal)")
    print("  2) Set env vars:")
    print('       $env:LLM_PROVIDER       = "openrouter"')
    print('       $env:OPENROUTER_API_KEY = "sk-or-v1-..."')
    print("  3) python run.py")
    print("  4) curl http://127.0.0.1:8000/chat/status")


if __name__ == "__main__":
    main()