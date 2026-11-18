"""Pure entitlement resolution (docs/05). No DB or framework imports."""

from dataclasses import dataclass

from app.platform.features import BOOL_FEATURES, LIMITS

# Read-only features stay available when a hotel is suspended; everything else resolves false.
WRITE_BLOCKED_STATUSES = {"SUSPENDED", "CANCELLED"}


@dataclass(frozen=True)
class FeatureValue:
    bool_value: bool | None = None
    int_limit: int | None = None
    present: bool = True


def resolve(plan: dict[str, FeatureValue], overrides: dict[str, FeatureValue], subscription_status: str) -> dict:
    """effective(hotel, key) = override ?? plan_feature. Suspended => every feature false, limits 0."""
    blocked = subscription_status in WRITE_BLOCKED_STATUSES
    features: dict[str, bool] = {}
    for key in BOOL_FEATURES:
        src = overrides.get(key) or plan.get(key)
        features[key] = False if blocked else bool(src and src.bool_value)
    limits: dict[str, int | None] = {}
    for key in LIMITS:
        src = overrides.get(key) or plan.get(key)
        if blocked:
            limits[key] = 0
        elif src is None:
            limits[key] = 0  # not in plan => not allowed
        else:
            limits[key] = src.int_limit  # None = unlimited
    return {"features": features, "limits": limits, "status": subscription_status, "read_only": blocked}


def within_limit(limit: int | None, current_count: int) -> bool:
    return limit is None or current_count < limit
