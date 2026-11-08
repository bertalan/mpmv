"""Gunicorn logger that redacts action link tokens from the access log (CORE.15).

The web role runs gunicorn with `--logger-class mpmv.gunicorn_logging.RedactingLogger`
(deploy/container/entrypoint.sh). Every access log atom (request line, path, referer, headers)
goes through `redact_secret_paths`, so an emailed link that a mail scanner prefetches never
writes its live token to stdout.
"""

import datetime as dt
from typing import Any

from gunicorn.glogging import Logger

from mpmv.log_redaction import redact_secret_paths


class RedactingLogger(Logger):  # type: ignore[misc]  # gunicorn ships no type information
    def atoms(
        self, resp: Any, req: Any, environ: Any, request_time: dt.timedelta
    ) -> dict[str, Any]:
        atoms: dict[str, Any] = super().atoms(resp, req, environ, request_time)
        return {
            key: redact_secret_paths(value) if isinstance(value, str) else value
            for key, value in atoms.items()
        }
