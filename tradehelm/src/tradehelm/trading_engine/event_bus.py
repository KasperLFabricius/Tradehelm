"""Simple in-memory event bus for engine events."""
from collections import defaultdict
from typing import Any, Callable


class EventBus:
    """Tiny pub/sub used by engine internals."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self._subs[event_type].append(fn)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        for fn in self._subs[event_type]:
            fn(payload)
