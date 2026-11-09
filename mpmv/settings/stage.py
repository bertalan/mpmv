"""STAGE: same as LIVE, fed with synthetic data only (spec 12)."""

from .live import *  # noqa: F403

# Synthetic data only: the demo seed (ARC.10) runs when the environment sets MPMV_ALLOW_SEED=1.
LIVE_DATA = False
