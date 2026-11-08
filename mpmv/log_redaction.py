"""Keep secrets that travel in URL paths out of the logs (CORE.15).

A secure action link carries its token in the path (`/account/link/<token>/`), and request logs
write paths: Django's `django.request` warnings (a 404 or 405 on the link) and gunicorn's access
log (`mpmv.gunicorn_logging`). Both pass their text through `redact_secret_paths`.
"""

import logging
import re
from typing import Final

REDACTED: Final = "[redacted]"
# The segment after /account/link/, except the fixed Continue route, which holds no secret.
_SECRET_PATH: Final = re.compile(r"(/account/link/)(?!continue(?:[/?#\s\"']|$))[^/?#\s\"']+")


def redact_secret_paths(text: str) -> str:
    """`text` with the secret segment of every action link path replaced by `REDACTED`."""
    return _SECRET_PATH.sub(rf"\g<1>{REDACTED}", text)


class RedactSecretPaths(logging.Filter):
    """Rewrite a record whose message names an action link path; never drops a record.

    On a logger it runs before the record propagates, so every handler (console, admin email)
    sees the redacted message.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secret_paths(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True
