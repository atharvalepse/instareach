"""ServerChannel — sends via instagrapi from the server (the existing approach).

Kept behind the SendChannel interface so it's swappable. instagrapi is imported
lazily inside send(): the module stays importable (and the rest of the backend
stays testable) even when instagrapi isn't installed.

NOTE on positioning: this is the higher-ban-risk path (datacenter IP + server
login + password handling). It exists for parity, but the recommended default
for safety is the future BrowserChannel. Rate-limiting/pacing is the caller's
job (the send loop), not this class's.
"""

from .base import SendChannel, SendResult


class ServerChannel(SendChannel):
    name = "server"

    def __init__(self, username: str, password: str, session_path: str = None):
        self.username = username
        self.password = password
        self.session_path = session_path or f"session_{username}.json"
        self._client = None

    def _login(self):
        if self._client is not None:
            return self._client
        try:
            from instagrapi import Client  # lazy: optional dependency
        except ImportError as e:
            raise RuntimeError("instagrapi not installed — `pip install instagrapi`") from e
        import os
        client = Client()
        if os.path.exists(self.session_path):
            client.load_settings(self.session_path)
        client.login(self.username, self.password)
        client.dump_settings(self.session_path)
        self._client = client
        return client

    def healthy(self) -> bool:
        try:
            self._login()
            return True
        except Exception:
            return False

    def send(self, username: str, message: str) -> SendResult:
        try:
            client = self._login()
            user_id = client.user_id_from_username(username)
            client.direct_send(message, [user_id])
            return SendResult(ok=True, detail="sent via instagrapi")
        except Exception as e:
            msg = str(e).lower()
            blocked = any(k in msg for k in ("429", "checkpoint", "feedback_required", "please wait"))
            return SendResult(ok=False, detail=str(e), blocked=blocked)
