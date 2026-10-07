"""Single-command runner.  python run.py  ->  http://127.0.0.1:8000"""
import os
from dotenv import load_dotenv

load_dotenv()

import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "1") == "1"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)