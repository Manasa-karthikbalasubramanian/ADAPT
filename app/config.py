"""Central configuration: paths and business thresholds.

Nothing in here is tied to a specific dataset (no hard-coded dates/SKUs/creatives):
events are detected from the data itself.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
STATE_DIR = Path(os.getenv("STATE_DIR", ROOT / "app" / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --- decision thresholds -------------------------------------------------
ROAS_SCALE_THRESHOLD = 8.0      # blended ROAS needed to scale a campaign
ROAS_PAUSE_THRESHOLD = 2.0      # below this -> cut budget
MARGIN_MIN           = 0.30     # never scale below this gross margin
SCALE_SCORE_MIN      = 0.65     # opportunity score needed to scale
SCALE_STEP_PCT       = 20
CUT_STEP_PCT         = 30
STOCK_HEADROOM_MIN   = 50       # units
STOCK_COVER_MIN_DAYS = 3.0      # days of stock cover needed to scale

# --- anomaly thresholds --------------------------------------------------
CVR_CRASH_DROP_PCT   = 0.20     # day CVR this far below median = crash day
PLATFORM_SHIFT_PCT   = 0.15     # platform ROAS move (pre vs post change-point)
PLATFORM_RELATIVE_PCT = 0.10    # ...and this far from the other platforms' move
ATTRIBUTION_GAP_PCT  = 0.10     # platform-reported vs ledger conversions
CREATIVE_FATIGUE_MIN = 0.60
MIN_SEGMENT_DAYS     = 3        # min days on each side of a change-point

# --- allocator -----------------------------------------------------------
ALLOC_MIN_PCT = 0.02            # drop campaigns that would get <2%
ALLOC_MAX_PCT = 0.25            # no campaign gets >25% (concentration guard)
