"""make_backup.py — zip the entire project including dotfiles (.env)."""
import zipfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = Path.home() / "Desktop" / "ADAPT_backend_BACKUP.zip"

# directories/files to skip inside the zip
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "dist", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}

count = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for abs_p in ROOT.rglob("*"):
        if not abs_p.is_file():
            continue
        rel = abs_p.relative_to(ROOT)
        parts = set(rel.parts)
        if parts & SKIP_DIRS:
            continue
        if abs_p.suffix in SKIP_SUFFIX:
            continue
        # preserve structure under "ADAPT backend/"
        arcname = Path("ADAPT backend") / rel
        zf.write(abs_p, arcname)
        count += 1

size_mb = OUT.stat().st_size / (1024 * 1024)
print(f"[ok] {OUT}")
print(f"[ok] {count} files  ({size_mb:.2f} MB)")
print("\nCritical files check:")
with zipfile.ZipFile(OUT) as zf:
    names = zf.namelist()
    for needle in [".env", "ad_spend.csv", "sales.csv", "inventory.csv",
                   "sku_margins.csv", "creative_assets.csv",
                   "run.py", "requirements.txt"]:
        hit = any(needle in n for n in names)
        mark = "OK " if hit else "MISSING"
        print(f"  [{mark}] {needle}")