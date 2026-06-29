"""DryRunChannel — records sends instead of delivering them.

The default channel for development, tests, and 'preview a campaign without
touching Instagram'. Can be told to fail or block specific usernames so the
send loop's error/stop paths are testable.
"""

from .base import SendChannel, SendResult


class DryRunChannel(SendChannel):
    name = "dryrun"

    def __init__(self, fail=None, block=None):
        self.sent = []                  # list of (username, message)
        self._fail = set(fail or [])    # usernames that return ok=False
        self._block = set(block or [])  # usernames that return blocked=True

    def send(self, username: str, message: str) -> SendResult:
        if username in self._block:
            return SendResult(ok=False, detail="simulated block", blocked=True)
        if username in self._fail:
            return SendResult(ok=False, detail="simulated failure")
        self.sent.append((username, message))
        return SendResult(ok=True, detail="dry-run recorded")
