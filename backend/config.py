import os


GATEWAY_PREFIX = (os.environ.get("FLYMAIL_BASE_PATH", "") or "").rstrip("/")
SESSION_COOKIE_NAME = os.environ.get("FLYMAIL_SESSION_COOKIE", "flymail_session")
