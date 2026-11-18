"""Post-commit fan-out: realtime events to the hub, push intents to FCM. Called only after COMMIT."""

from app.notify import push
from app.realtime.hub import hub

PUSH = "push"  # pseudo-channel used by ctx.emit for push intents


def publish_all(events: list[tuple[str, dict]]) -> None:
    for channel, event in events:
        if channel == PUSH:
            push.fire(event)
        else:
            hub.publish(channel, event)
