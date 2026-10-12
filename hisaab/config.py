"""Paths and tuning constants. Every number that changes behaviour lives here, with the reason it has that value."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
SEED_DIR = PKG / "seed"

# Where the user's private data lives. Overridable so tests can use a temp folder.
DATA_DIR = Path(os.environ.get("HISAAB_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "hisaab.db"
MODELS_DIR = DATA_DIR / "models"
UPLOAD_DIR = DATA_DIR / "uploads"

HOST = "127.0.0.1"                     # never 0.0.0.0: statements must not be reachable from the network
# Extra host names the server answers to, besides localhost. Empty on your own computer; a hosted demo adds its public name with
# HISAAB_ALLOWED_HOSTS=a.example.com,b.example.com. On Render the service's own name (RENDER_EXTERNAL_HOSTNAME) is added automatically.
EXTRA_HOSTS = {h.strip() for h in os.environ.get("HISAAB_ALLOWED_HOSTS", "").split(",") if h.strip()}
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    EXTRA_HOSTS.add(os.environ["RENDER_EXTERNAL_HOSTNAME"])
PORT = int(os.environ.get("PORT", 5056))
MAX_UPLOAD_MB = 15

# ---- category model (TF-IDF + logistic regression) ---------------------------------------------------------------
# Hyper-parameters of the category model.  C = inverse regularisation strength (larger = weaker penalty); char_range = which character n-gram
# lengths of the shop name are used; ctx_weight = how much the "context" tokens (hour, amount band, UPI-address shape) count relative to the name.
CATEGORY_MODEL = {"C": 5.0, "char_range": (2, 5), "ctx_weight": 0.5}

# ---- categorisation thresholds -------------------------------------------------------------------------------
# A model prediction is *accepted* (shown as a confident label, status "auto") when its top probability is at least this.
# Chosen from a precision/coverage analysis: at 0.55 the accepted answers are right about 98% of the time and cover roughly 70-80% of all rows.
CAT_ACCEPT = 0.55
# Below CAT_ACCEPT but above this, the label is shown as a *suggestion* ("please confirm"). Below it: "needs review".
CAT_SUGGEST = 0.30

# P2P-vs-merchant: probability of "person" above/below which we are confident (display only).
KIND_PERSON_HI = 0.70
KIND_MERCHANT_LO = 0.30
# Below this confidence a shop-or-person call is a 'close call': it is sent to Review and categories derived from it are only 'suggested'.
# 0.80: every wrong shop-or-person call seen in validation had a confidence below it, and it means asking about ~10% of merchants.
KIND_CONF_AUTO = 0.80

# ---- learning ------------------------------------------------------------------------------------------------
USER_SAMPLE_WEIGHT = 6.0     # a label the user typed counts this many times more than a seed example
SEED_SAMPLES_PER_BRAND = 10
SEED_EXAMPLES_PER_WORD = 6     # cold-start examples generated per shop-type word in the lexicon

# ---- entity resolution ---------------------------------------------------------------------------------------
NAME_MERGE_THRESHOLD = 0.86  # two merchant names at/above this similarity are the same shop (see core/normalize.py)

# ---- recurring / anomaly ------------------------------------------------------------------------------------
RECUR_MIN_OCCURRENCES = 3
ANOMALY_Z = 3.5              # robust z-score (MAD based) above which an amount is "unusual" for that merchant/category
DUPLICATE_WINDOW_MIN = 10    # same merchant + same amount within this many minutes => possible duplicate charge

# ---- web ----------------------------------------------------------------------------------------------------
CUSTOM_HEADER = "X-Hisaab"   # every state-changing request must carry it (cheap CSRF protection, see web/app.py)


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODELS_DIR, UPLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)
