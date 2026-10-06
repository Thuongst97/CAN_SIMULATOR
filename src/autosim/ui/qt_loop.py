"""Runs the SOME/IP asyncio event loop inside a QThread, next to the Qt GUI event loop."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread


class QtLoopThread(QThread):
    def __init__(self, target: Callable[[], None], name: str = "") -> None:
        super().__init__()
        self._target = target
        if name:
            self.setObjectName(name)

    def run(self) -> None:          # executes on the new thread; blocks in loop.run_forever()
        self._target()

    def join(self, timeout: float) -> None:
        self.wait(int(timeout * 1000))


def qt_thread_factory(target: Callable[[], None], name: str) -> QtLoopThread:
    return QtLoopThread(target, name)
