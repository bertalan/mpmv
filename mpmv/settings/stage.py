"""STAGE: same as LIVE, fed with synthetic data only (spec 12)."""

from .live import *  # noqa: F403

# Synthetic data only: the demo seed (ARC.10) runs when the environment sets MPMV_ALLOW_SEED=1.
LIVE_DATA = False
STAGE = True

# STAGE is never indexed (SEO.8, D-35); Caddy sends the header too, as defence in depth.
FORCE_NOINDEX = True
