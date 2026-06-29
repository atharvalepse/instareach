"""Send channels — the swappable 'hands' that actually deliver a DM."""

from .base import SendChannel, SendResult
from .dryrun import DryRunChannel
from .server import ServerChannel

__all__ = ["SendChannel", "SendResult", "DryRunChannel", "ServerChannel"]
