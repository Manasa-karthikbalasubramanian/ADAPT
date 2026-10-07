from fastapi import APIRouter
from app.data.loader import load_raw, load_unified, to_records

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get("/raw")
def get_raw():
    return {k: to_records(v) for k, v in load_raw().items()}


@router.get("/unified")
def get_unified():
    return {k: to_records(v) for k, v in load_unified().items()}


@router.get("/summary")
def summary():
    """Row counts + date range per source - handy for an 'ingest' animation."""
    u = load_unified()
    out = {}
    for k, df in u.items():
        entry = {"rows": int(len(df)), "columns": list(df.columns)}
        if "date" in df.columns:
            entry["from"] = df["date"].min().strftime("%Y-%m-%d")
            entry["to"] = df["date"].max().strftime("%Y-%m-%d")
        out[k] = entry
    return out
