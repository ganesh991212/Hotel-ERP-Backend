"""Pure subscription lifecycle (docs/05): TRIAL -> ACTIVE -> PAST_DUE (grace) -> SUSPENDED."""

from datetime import datetime, timedelta


def effective_status(
    status: str,
    now: datetime,
    trial_ends_at: datetime | None,
    current_period_end: datetime | None,
    grace_days: int,
) -> str:
    if status in ("SUSPENDED", "CANCELLED"):
        return status
    if status == "TRIAL":
        if trial_ends_at and now > trial_ends_at:
            return "SUSPENDED" if now > trial_ends_at + timedelta(days=grace_days) else "PAST_DUE"
        return "TRIAL"
    if status in ("ACTIVE", "PAST_DUE"):
        if current_period_end and now > current_period_end:
            return "SUSPENDED" if now > current_period_end + timedelta(days=grace_days) else "PAST_DUE"
        return "ACTIVE"
    return status
