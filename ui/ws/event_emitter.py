"""Async event bridge between background pipeline jobs and WebSocket clients."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineEventEmitter:
    """Broadcast pipeline status events to all connected WebSocket clients."""

    max_history: int = 200
    _queues: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_history)
        for event in self._history[-self.max_history :]:
            queue.put_nowait(event)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    async def emit_async(
        self,
        step: int,
        status: str,
        message: str,
        elapsed_ms: int = 0,
        level: str = "INFO",
        **extra: Any,
    ) -> None:
        event = {
            "step": step,
            "status": status,
            "message": message,
            "elapsed_ms": elapsed_ms,
            "level": level,
            "ts": time.time(),
            **extra,
        }
        self._history.append(event)
        self._history = self._history[-self.max_history :]
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)

    def emit(
        self,
        step: int,
        status: str,
        message: str,
        elapsed_ms: int = 0,
        level: str = "INFO",
        **extra: Any,
    ) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.emit_async(step, status, message, elapsed_ms, level, **extra),
                self._loop,
            )

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

