"""Pure chef routing (docs/04-kitchen-routing.md). No DB or framework imports.

assign(snapshot, config) -> chef_id | None. The caller holds the per-outlet lock.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime

MIN_RATING_SAMPLES = 10


@dataclass(frozen=True)
class ChefState:
    chef_id: str
    on_shift: bool = True
    active: bool = True
    queue_minutes: tuple[int, ...] = ()  # remaining prep minutes of each order in the queue
    last_completed_at: datetime | None = None
    rating: float | None = None
    rating_n: int = 0

    @property
    def est_wait(self) -> int:
        return sum(self.queue_minutes)

    @property
    def queue_len(self) -> int:
        return len(self.queue_minutes)


@dataclass(frozen=True)
class RoutingConfig:
    max_queue_orders: int = 3
    rating_band_minutes: int = 5
    rating_weighted: bool = False


@dataclass
class Snapshot:
    chefs: list[ChefState] = field(default_factory=list)


def _idle_key(c: ChefState) -> tuple:
    # Longest idle first: a chef who never completed counts as idle the longest.
    ts = c.last_completed_at.timestamp() if c.last_completed_at else float("-inf")
    return (ts, c.chef_id)


def assign(snapshot: Snapshot, config: RoutingConfig, exclude: frozenset[str] = frozenset()) -> str | None:
    eligible = [
        c
        for c in snapshot.chefs
        if c.active and c.on_shift and c.chef_id not in exclude and c.queue_len < config.max_queue_orders
    ]
    if not eligible:
        return None
    best = min(c.est_wait for c in eligible)
    band = config.rating_band_minutes
    candidates = [c for c in eligible if c.est_wait <= best + band]
    if config.rating_weighted and band > 0:
        rated = [c for c in candidates if c.rating is not None and c.rating_n >= MIN_RATING_SAMPLES]
        if rated:
            top = max(c.rating for c in rated)  # type: ignore[type-var]
            tied = [c for c in rated if c.rating == top]
            return sorted(tied, key=_idle_key)[0].chef_id
    # Without rating, the band does not apply: lowest wait wins, then longest idle, then lowest id.
    lowest = [c for c in eligible if c.est_wait == best]
    return sorted(lowest, key=_idle_key)[0].chef_id


def with_order(snapshot: Snapshot, chef_id: str, prep_minutes: int) -> Snapshot:
    return Snapshot(
        [
            replace(c, queue_minutes=(*c.queue_minutes, prep_minutes)) if c.chef_id == chef_id else c
            for c in snapshot.chefs
        ]
    )


def assign_many(
    snapshot: Snapshot, orders: list[tuple[str, int]], config: RoutingConfig
) -> list[tuple[str, str | None]]:
    """Assign orders in the given order, each seeing the previous assignments (examples 11, 13)."""
    out = []
    for order_id, minutes in orders:
        chef = assign(snapshot, config)
        out.append((order_id, chef))
        if chef:
            snapshot = with_order(snapshot, chef, minutes)
    return out
