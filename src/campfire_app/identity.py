"""Current-user identity from Databricks Apps forwarded headers.

Databricks Apps sit behind the workspace's auth proxy and forward the
signed-in user on every request — the app never asks for a name.
"""

import hashlib
import os

from fastapi import Request

AVATAR_PALETTE = ["#7a8450", "#b8912f", "#b06a4a", "#6b6555"]


def _display_name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) or email


def _initials(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "?"


def avatar_color(key: str) -> str:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return AVATAR_PALETTE[h % len(AVATAR_PALETTE)]


def current_user(request: Request) -> dict:
    email = (
        request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Forwarded-Preferred-Username")
        or os.environ.get("CAMPFIRE_DEV_USER", "dev@example.com")
    )
    user_id = request.headers.get("X-Forwarded-User") or email
    name = _display_name_from_email(email)
    return {
        "id": user_id,
        "email": email,
        "name": name,
        "initials": _initials(name),
        "avatarColor": avatar_color(email),
    }
