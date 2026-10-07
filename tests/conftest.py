"""Isolate persisted state so tests never touch (or depend on) real logs."""
import os
import tempfile

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="d2c_state_")
