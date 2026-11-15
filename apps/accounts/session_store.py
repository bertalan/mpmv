"""Django's database session backend, with the per-user session index kept in step (CORE.24).

Still `django.contrib.sessions.backends.db`: only the key rotation and the deletion also touch
`UserSession`, so a rotated key (login, a second-factor verification, a password change) keeps
its index row and a deleted session (logout, an ended session) loses it in the same call.

The index bookkeeping (`index_session`, `touch_session`, `prune_session_index`) lives here with
the backend: it is part of the session store, not a business change, so it is not audited and
has no dry run. Ending sessions is a service (`services.end_sessions`), audited.
"""

import datetime as dt
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.contrib.sessions.backends import db
from django.contrib.sessions.backends.base import SessionBase
from django.utils import timezone

from . import engine

if TYPE_CHECKING:
    from .models import User

# CORE.24: the user's session generation at login (see `User.session_generation`).
SESSION_GENERATION_KEY: Final = "session_generation"


class SessionStore(db.SessionStore):
    _rotating = False

    def cycle_key(self) -> None:
        old = self.session_key
        self._rotating = True
        try:
            super().cycle_key()
        finally:
            self._rotating = False
        new = self.session_key
        if old and new and old != new:
            from .models import UserSession

            UserSession.objects.filter(session_key=old).update(session_key=new)

    def delete(self, session_key: str | None = None) -> None:
        key = session_key if session_key is not None else self.session_key
        super().delete(session_key)
        if key and not self._rotating:
            from .models import UserSession

            UserSession.objects.filter(session_key=key).delete()

    @classmethod
    def clear_expired(cls, *args: Any, **kwargs: Any) -> None:
        super().clear_expired(*args, **kwargs)
        from django.contrib.sessions.models import Session

        from .models import UserSession

        live = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
        UserSession.objects.exclude(session_key__in=live).delete()


def index_session(
    user: "User",
    session: SessionBase,
    *,
    user_agent: str | None,
    ip: str | None,
    now: dt.datetime | None = None,
) -> None:
    """Record or refresh `session` of `user` in the index (bookkeeping, not audited). Only the
    index row is written: the session data is left alone."""
    key = session.session_key
    if not key:
        return
    from .models import UserSession

    UserSession.objects.update_or_create(
        session_key=key, defaults={"user": user, **_row_values(user_agent, ip, now)}
    )


def _row_values(user_agent: str | None, ip: str | None, now: dt.datetime | None) -> dict[str, Any]:
    device = engine.device_summary(user_agent)
    return {
        "last_seen": now or timezone.now(),
        "browser": device.browser,
        "system": device.system,
        "network": engine.network_prefix(ip),
    }


def touch_session(
    user: "User", session: SessionBase, *, user_agent: str | None, ip: str | None
) -> None:
    """Refresh the index row of `session` when it is older than `SESSION_TOUCH_SECONDS`, or add
    it when missing (a session from before the index).

    One conditional UPDATE that matches nothing on most requests. It never writes to the session
    itself, so it never extends the session's expiry: a touch cannot make a session rolling.
    """
    key = session.session_key
    if not key:
        return
    from .models import UserSession

    values = _row_values(user_agent, ip, None)
    stale = values["last_seen"] - dt.timedelta(seconds=settings.SESSION_TOUCH_SECONDS)
    rows = UserSession.objects.filter(session_key=key, user=user)
    if rows.filter(last_seen__lte=stale).update(**values) or rows.exists():
        return
    UserSession.objects.bulk_create(
        [UserSession(user=user, session_key=key, **values)], ignore_conflicts=True
    )


def generation_matches(user: "User", session: SessionBase) -> bool:
    """The session carries the user's current generation (a session from before the generation
    existed counts as generation 0)."""
    stored = session.get(SESSION_GENERATION_KEY, 0)
    return isinstance(stored, int) and stored == user.session_generation


def prune_session_index(user: "User") -> None:
    """Drop the index rows of `user` whose session is gone or expired."""
    from django.contrib.sessions.models import Session

    from .models import UserSession

    live = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
    UserSession.objects.filter(user=user).exclude(session_key__in=live).delete()
