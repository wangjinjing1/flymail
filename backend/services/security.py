import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Request, Response

from config import SESSION_COOKIE_NAME


SESSION_MAX_AGE = 60 * 60 * 24 * 7


def _session_secret() -> bytes:
    secret = os.environ.get("FLYMAIL_SESSION_SECRET", "").strip()
    if len(secret) < 16:
        raise RuntimeError("FLYMAIL_SESSION_SECRET must be at least 16 characters")
    return secret.encode("utf-8")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('ascii')}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, _encoded = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    expected = hash_password(password, salt)
    return hmac.compare_digest(expected, password_hash)


def _sign_payload(payload: bytes) -> str:
    return hmac.new(_session_secret(), payload, hashlib.sha256).hexdigest()


def create_session_cookie(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = _sign_payload(payload_bytes)
    return f"{encoded}.{signature}"


def parse_session_cookie(raw_value: str | None) -> dict | None:
    if not raw_value or "." not in raw_value:
        return None
    encoded, signature = raw_value.rsplit(".", 1)
    try:
        payload_bytes = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        expected = _sign_payload(payload_bytes)
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(payload_bytes.decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_cookie(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def get_session_user_id(request: Request) -> str | None:
    payload = parse_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if not payload:
        return None
    return payload.get("uid") or None
