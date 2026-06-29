"""SendChannel — one interface, many hands.

The campaign brain only ever talks to this. Today: DryRunChannel (tests/dev)
and ServerChannel (instagrapi). Later: BrowserChannel that executes the send in
the user's real logged-in extension session — the safe path. Because they share
this contract, the brain never changes when we switch hands.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SendResult:
    ok: bool
    detail: str = ""
    blocked: bool = False   # True => hard stop the whole run (429 / checkpoint)


class SendChannel(ABC):
    name = "abstract"

    @abstractmethod
    def send(self, username: str, message: str) -> SendResult:
        """Deliver `message` to @username. Never raises — returns a SendResult."""
        raise NotImplementedError

    def healthy(self) -> bool:
        """Cheap readiness check (logged in / reachable). Default: assume ok."""
        return True
