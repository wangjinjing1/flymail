import os


def apply_proxy_env() -> None:
    """Map FlyMail proxy env vars to standard proxy env vars used by HTTP clients."""
    mappings = {
        "FLYMAIL_HTTP_PROXY": "HTTP_PROXY",
        "FLYMAIL_HTTPS_PROXY": "HTTPS_PROXY",
        "FLYMAIL_ALL_PROXY": "ALL_PROXY",
        "FLYMAIL_NO_PROXY": "NO_PROXY",
    }
    for source, target in mappings.items():
        value = (os.environ.get(source, "") or "").strip()
        if value:
            os.environ[target] = value
            os.environ[target.lower()] = value
