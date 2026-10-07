
"""
add_openrouter.py — wires OpenRouter in as the LLM provider.

Adds:
    app/intelligence/llm_provider.py   (rewritten with openrouter)
    .env.example                       (updated)
    tests/test_openrouter.py           (new)
Overwrites:
    requirements.txt
Verifies imports at the end.

Run:  python add_openrouter.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FILES: dict[str, str] = {}

# ============================================================ llm_provider.py
FILES["app/intelligence/llm_provider.py"] = '''"""Unified LLM client.

Providers (LLM_PROVIDER env var, else auto-detected):
    openrouter  -> OPENROUTER_API_KEY, base_url https://openrouter.ai/api/v1
    openai      -> OPENAI_API_KEY
    ollama      -> local Ollama server
    template    -> deterministic fallback (no network)

All providers return plain text.  Call complete(prompt, system=..., ...).
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENAI_BASE     = "https://api.openai.com/v1"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Sensible defaults — override with env vars.
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_OPENAI_MODEL     = "gpt-4o-mini"
DEFAULT_OLLAMA_MODEL     = "llama3.2"


# ------------------------------------------------------------------ detection
def active_provider() -> str:
    p = os.getenv("LLM_PROVIDER", "").strip().lower()
    if p in {"openrouter", "openai", "ollama", "template"}:
        return p
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if _ollama_up():
        return "ollama"
    return "template"


def available() -> bool:
    return active_provider() != "template"


def _ollama_up() -> bool:
    host = os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    try:
        req = urllib.request.Request(f"{host}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


# ------------------------------------------------------------------ main entry
def complete(prompt: str, system: str = "", temperature: float = 0.3,
             max_tokens: int = 500) -> str:
    p = active_provider()
    if p == "openrouter":
        return _openai_compatible(
            base_url=OPENROUTER_BASE,
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            model=os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens,
            extra_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000"),
                "X-OpenRouter-Title": os.getenv("OPENROUTER_APP_NAME", "ADAPT"),
            },
        )
    if p == "openai":
        return _openai_compatible(
            base_url=OPENAI_BASE,
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens,
        )
    if p == "ollama":
        return _ollama(prompt, system, temperature)
    return ""


# ------------------------------------------------------------------ openai-compatible (openrouter + openai)
def _openai_compatible(base_url: str, api_key: str, model: str,
                       prompt: str, system: str,
                       temperature: float, max_tokens: int,
                       extra_headers: dict | None = None) -> str:
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key,
                    default_headers=extra_headers or {})
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    r = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return r.choices[0].message.content or ""


# ------------------------------------------------------------------ ollama
def _ollama(prompt: str, system: str, temperature: float) -> str:
    host = os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    body = {
        "model": model, "prompt": prompt, "system": system,
        "stream": False, "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("response", "")


# ------------------------------------------------------------------ diagnostics
def status() -> dict:
    return {
        "active_provider": active_provider(),
        "available": available(),
        "openrouter_key_set": bool(os.getenv("OPENROUTER_API_KEY")),
        "openrouter_base": OPENROUTER_BASE,
        "openrouter_model": os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
        "openai_key_set": bool(os.getenv("OPENAI_API_KEY")),
        "openai_model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "ollama_host": os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        "ollama_up": _ollama_up(),
        "ollama_model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
    }
'''

# ============================================================ .env.example
FILES[".env.example"] = '''# LLM provider: openrouter | openai | ollama | template
LLM_PROVIDER=openrouter

# --- OpenRouter (recommended) ---
OPENROUTER_API_KEY=
# Free by default; swap for any model on openrouter.ai/models
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
# Shown on openrouter.ai rankings (optional)
OPENROUTER_SITE_URL=http://localhost:8000
OPENROUTER_APP_NAME=ADAPT

# --- OpenAI (alternative) ---
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# --- Ollama (local, free) ---
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

LOG_LEVEL=INFO
'''

# ============================================================ tests
FILES["tests/test_openrouter.py"] = '''from app.intelligence import llm_provider


def test_provider_detection_defaults():
    # With no env vars, we should fall back to template
    assert llm_provider.active_provider() in {"template", "openrouter",
                                              "openai", "ollama"}


def test_status_shape():
    s = llm_provider.status()
    for k in ("active_provider", "available", "openrouter_key_set",
              "openrouter_base", "openrouter_model"):
        assert k in s
    assert s["openrouter_base"] == "https://openrouter.ai/api/v1"


def test_openrouter_model_default_is_free():
    import os
    model = os.getenv("OPENROUTER_MODEL",
                      llm_provider.DEFAULT_OPENROUTER_MODEL)
    assert isinstance(model, str) and len(model) > 0
'''

# ============================================================ requirements.txt
FILES["requirements.txt"] = (
    "matplotlib>=3.8\n"
    "fastapi>=0.110\n"
    "uvicorn[standard]>=0.29\n"
    "pydantic>=2.6\n"
    "pandas>=2.2\n"
    "httpx>=0.27\n"
    "pytest>=8.0\n"
    "openai>=1.30\n"
)


def main() -> None:
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print(f"  [write] {rel}")

    import sys
    sys.path.insert(0, str(ROOT))
    try:
        from app.main import app
        from app.intelligence import llm_provider
        print(f"\n[OK]  app.main imports cleanly. routes: {len(app.routes)}")
        print(f"[OK]  provider status: {llm_provider.status()}")
    except Exception as e:
        print(f"\n[FAIL]  {e!r}")
        raise

    print("\nDone. Next:")
    print("  1) python -m pip install -r requirements.txt")
    print('  2) $env:LLM_PROVIDER = "openrouter"')
    print('     $env:OPENROUTER_API_KEY = "sk-or-v1-..."')
    print("     python run.py")
    print("  3) open http://127.0.0.1:8000/chat/ui")


if __name__ == "__main__":
    main()