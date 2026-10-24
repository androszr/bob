"""token_health: when the watch loop relaunches Teams, when it alerts, and the JWT expiry check."""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "teams_agent"))

import token_health  # noqa: E402


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.sig"


def test_expiry_is_read_from_the_jwt_and_garbage_is_unknown():
    assert token_health.token_expiry(_jwt({"exp": 1000})) == 1000.0
    assert token_health.token_expiry("not-a-jwt") is None
    assert token_health.token_expiry(_jwt({"aud": "x"})) is None
    assert token_health.expires_soon(_jwt({"exp": 1000}), now=500) is True  # 500 s < 10 min margin
    assert token_health.expires_soon(_jwt({"exp": 5000}), now=500) is False
    assert token_health.expires_soon(_jwt({"exp": 1000}), now=999, margin_s=0) is False
    assert token_health.expires_soon(_jwt({"exp": 1000}), now=1001, margin_s=0) is True  # already expired
    assert token_health.expires_soon("", now=0) is False


def test_stuck_token_relaunches_once_then_alerts_once():
    h = token_health.TokenHealth()
    assert h.after_refresh(False, 0) is None
    assert h.after_refresh(False, 60) == "relaunch"
    assert h.after_refresh(False, 120) == "alert"
    assert h.after_refresh(False, 180) is None, "no repeated alerts within one incident"
    assert h.dead_minutes(300) == 5


def test_new_token_after_escalation_reports_recovery_and_resets():
    h = token_health.TokenHealth()
    h.after_refresh(False, 0)
    assert h.after_refresh(False, 60) == "relaunch"
    assert h.after_refresh(True, 90) == "recovered"
    assert (h.failed_refreshes, h.dead_since, h.dead_minutes(1000)) == (0, None, 0)
    # A new incident escalates again from scratch.
    h.after_refresh(False, 2000)
    assert h.after_refresh(False, 2060) == "relaunch"


def test_a_single_failed_refresh_then_a_new_token_is_not_news():
    h = token_health.TokenHealth()
    assert h.after_refresh(False, 0) is None
    assert h.after_refresh(True, 30) is None


_BACKOFF = (0, 120, 300, 900)  # mirrors cli._NOOP_REFRESH_BACKOFF_S
_MIN_COOLDOWN = 30


def _simulate(token: str, start: float, end: float, flagged_401):
    """The watch loop's refresh block, 5 s ticks; the browser always hands back the same token."""
    last_refresh, streak, health, events = float("-inf"), 0, token_health.TokenHealth(), []
    t = start
    while t < end:
        base = max(_MIN_COOLDOWN, _BACKOFF[min(streak, len(_BACKOFF) - 1)])
        plan = token_health.plan_refresh(now=t, token=token, flagged_401=flagged_401(t),
                                         since_last_refresh=t - last_refresh, base_cooldown=base)
        if plan["needs_401"] or plan["expiring"]:
            last_refresh = t
            events.append(("refresh", t, streak))
            if token_health.counts_for_backoff(changed=False, needs_401=plan["needs_401"], expired=plan["expired"]):
                streak += 1
            if plan["expired"]:
                action = health.after_refresh(False, t)
                if action:
                    events.append((action, t, streak))
        t += 5
    return events


def test_dead_token_relaunches_within_minutes_of_expiry():
    exp = 10_000.0
    # Teams accepts ~5 min of clock skew, so the 401s start at exp + 300.
    events = _simulate(_jwt({"exp": exp}), exp - 900, exp + 900, lambda t: t > exp + 300)
    before = [e for e in events if e[1] < exp]
    assert before and all(e[0] == "refresh" and e[2] == 0 for e in before), \
        "proactive refreshes of a still valid token must not build up the back-off"
    relaunch = next(e for e in events if e[0] == "relaunch")
    assert relaunch[1] <= exp + 180, relaunch
    assert next(e for e in events if e[0] == "alert")[1] <= exp + 300


def test_401_with_a_valid_token_never_relaunches_or_alerts():
    events = _simulate(_jwt({"exp": 1e12}), 0, 3600, lambda t: True)
    assert {e[0] for e in events} == {"refresh"}
    gaps = [b[1] - a[1] for a, b in zip(events, events[1:], strict=False)]
    assert gaps[-1] >= 900, "the back-off still applies to 401s with another cause"
