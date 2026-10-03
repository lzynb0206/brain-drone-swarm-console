"""Generation-aware serial command queue for the Crazyflie gateway."""

from __future__ import annotations

import queue

from src.domain import SwarmCommand


class CrazyflieCommandQueue:
    """Centralize queue capacity, cancellation generations and shutdown sentinel."""

    def __init__(self, maxsize: int = 20) -> None:
        self.maxsize = int(maxsize)
        self._queue: queue.Queue[tuple[int, SwarmCommand] | None] = queue.Queue(
            maxsize=self.maxsize
        )
        self.generation = 0

    def reset(self) -> None:
        self._queue = queue.Queue(maxsize=self.maxsize)

    def invalidate(self) -> int:
        self.generation += 1
        return self.generation

    def enqueue(self, command: SwarmCommand) -> None:
        try:
            self._queue.put_nowait((self.generation, command))
        except queue.Full as exc:
            raise RuntimeError("飞行指令队列已满，请停止继续操作") from exc

    def get(self, timeout: float) -> tuple[int, SwarmCommand] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def drain(self) -> list[SwarmCommand]:
        sentinel_seen = False
        commands: list[SwarmCommand] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                sentinel_seen = True
            else:
                _generation, command = item
                commands.append(command)
        if sentinel_seen:
            self.close()
        return commands


__all__ = ["CrazyflieCommandQueue"]
