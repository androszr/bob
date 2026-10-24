"""Teams Bearer token health for the watch loop: when to relaunch the browser, when to raise an alert.

Failure mode this guards against: the token expired, every refresh (a click in the Playwright Teams page) handed
back the same dead token, and the agent went silent for every chat until a human restarted it. A fresh
browser context from session.json is what fixed it, so the loop now does that itself:

  * a refresh that changes the token clears everything ("recovered" when we had escalated);
  * RELAUNCH_AFTER refreshes in a row without a new token -> "relaunch" (once per incident);
  * ALERT_AFTER such refreshes (the relaunch did not help either) -> "alert" (once per incident) --
    a macOS notification, since Teams itself is unreachable without a token.

Pure state machine: no Playwright, no I/O -- cli.py performs the actions.
"""

from __future__ import annotations

import base64
import json

RELAUNCH_AFTER = 2
ALERT_AFTER = 3
# Refresh this long before the token's own expiry, instead of waiting for the first 401.
EXPIRY_MARGIN_S = 10 * 60


def token_expiry(token: str) -> float | None:
    """`exp` (epoch seconds) of a JWT, or None when it is not a readable JWT."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = claims.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:  # noqa: BLE001 -- any malformed token means "unknown expiry"
        return None


def expires_soon(token: str, now: float, margin_s: float = EXPIRY_MARGIN_S) -> bool:
    exp = token_expiry(token)
    return exp is not None and exp - now < margin_s


# A dead (expired) token is retried every minute: the 401 back-off is for 401s with another cause.
EXPIRED_RETRY_S = 60
# Proactive "expires soon" refreshes at most this often: Teams keeps handing back the cached token
# until its own renewal window.
PROACTIVE_MIN_GAP_S = 120


def plan_refresh(*, now: float, token: str, flagged_401: bool, since_last_refresh: float,
                 base_cooldown: float) -> dict:
    """What the watch loop should do this tick.

    expired   -- the token is past its `exp`; only refreshes made then count towards relaunch / alert
    needs_401 -- a 401 was seen and the (back-off) cooldown has passed
    expiring  -- a proactive refresh before the expiry is due
    """
    expired = expires_soon(token, now, 0)
    cooldown = EXPIRED_RETRY_S if expired else base_cooldown
    return {
        "expired": expired,
        "needs_401": flagged_401 and since_last_refresh >= cooldown,
        "expiring": since_last_refresh >= max(cooldown, PROACTIVE_MIN_GAP_S) and expires_soon(token, now),
    }


def counts_for_backoff(*, changed: bool, needs_401: bool, expired: bool) -> bool:
    """Does a refresh without a new token widen the 401 back-off? Not when it was merely proactive."""
    return not changed and (needs_401 or expired)


class TokenHealth:
    """Counts refreshes that did not produce a new token and says what to do next."""

    def __init__(self) -> None:
        self.failed_refreshes = 0
        self.dead_since: float | None = None
        self.relaunched = False
        self.alerted = False

    def after_refresh(self, changed: bool, now: float) -> str | None:
        """One of "recovered", "relaunch", "alert" or None."""
        if changed:
            escalated = self.relaunched or self.alerted
            self.failed_refreshes = 0
            self.relaunched = self.alerted = False
            self.dead_since = None
            return "recovered" if escalated else None
        self.failed_refreshes += 1
        if self.dead_since is None:
            self.dead_since = now
        if self.failed_refreshes >= RELAUNCH_AFTER and not self.relaunched:
            self.relaunched = True
            return "relaunch"
        if self.failed_refreshes >= ALERT_AFTER and not self.alerted:
            self.alerted = True
            return "alert"
        return None

    def dead_minutes(self, now: float) -> int:
        return 0 if self.dead_since is None else max(0, round((now - self.dead_since) / 60))
