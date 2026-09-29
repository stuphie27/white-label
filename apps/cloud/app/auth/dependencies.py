from __future__ import annotations

from fastapi import Request


def is_staff_authenticated(request: Request) -> bool:
    return bool(request.session.get("staff_authenticated"))
