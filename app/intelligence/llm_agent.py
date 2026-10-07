"""Narrative agent: optional LLM summary with a deterministic fallback.

Set OPENAI_API_KEY (and `pip install openai`) to enable the LLM path; any
failure silently falls back to the template (and is logged).
"""
import json
import logging
import os

log = logging.getLogger("llm_agent")


def _ident(f: dict) -> str:
    base = (f.get("sku") or f.get("creative_id") or f.get("campaign_id")
            or f.get("platform") or "site")
    if f.get("end_date") and f["end_date"] != f.get("date"):
        return f"{base} ({f['date']} -> {f['end_date']})"
    return f"{base} ({f['date']})" if f.get("date") else str(base)


def _template(diagnosis, opportunities, decisions) -> str:
    out = ["### Situation",
           f"Detected **{len(diagnosis)}** incidents across the account.", ""]
    for f in diagnosis[:6]:
        rc = (f.get("root_cause") or {}).get("probable_cause")
        out.append(f"- `{f['type']}` ({f.get('severity', '?')}) - {_ident(f)}"
                   + (f": {rc}" if rc else ""))
    out += ["", "### Top opportunities"]
    for o in opportunities[:3]:
        out.append(f"- **{o['campaign_id']}** ({o['platform']}): "
                   f"ROAS {o['roas']}, margin {o['weighted_margin_pct']:.0%}, "
                   f"score {o['opportunity_score']}")
    out += ["", "### Recommended actions"]
    for d in decisions[:6]:
        out.append(f"- `{d['action']}` -> **{d['target']}** - {d['rationale']}")
    return "\n".join(out)


def narrate(diagnosis, opportunities, decisions) -> str:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai(diagnosis, opportunities, decisions)
        except Exception as exc:
            log.warning("LLM narrative failed, using template: %s", exc)
    return _template(diagnosis, opportunities, decisions)


def _openai(diagnosis, opportunities, decisions) -> str:
    from openai import OpenAI
    client = OpenAI(timeout=20)
    compact = lambda xs, n: json.dumps(xs[:n], default=str)[:6000]
    prompt = (
        "You are a D2C marketing intelligence analyst. Write a concise "
        "executive brief (<=200 words) covering: situation, root causes, "
        "top 3 opportunities, recommended actions. Use only the data below.\n\n"
        f"Diagnosis: {compact(diagnosis, 8)}\n"
        f"Opportunities: {compact(opportunities, 5)}\n"
        f"Decisions: {compact(decisions, 8)}\n"
    )
    r = client.chat.completions.create(
        model="gpt-4o-mini", temperature=0.3,
        messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content
