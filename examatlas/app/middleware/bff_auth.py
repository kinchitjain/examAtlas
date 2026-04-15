"""
app/middleware/bff_auth.py

FastAPI middleware that rejects any request that doesn't carry the
X-BFF-Key header matching BFF_SECRET_KEY in .env.

This ensures the backend only accepts requests from the BFF proxy —
no browser or external tool can reach it directly.

In production the backend should be on a private network / firewall
so it's not reachable at all without the BFF, but this header check
provides a defence-in-depth layer.
"""
from __future__ import annotations

import os
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logging import get_logger

logger = get_logger(__name__)

# Endpoints that skip the check (used for k8s liveness probes, etc.)
_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class BFFAuthMiddleware(BaseHTTPMiddleware):
    """
    Rejects requests that lack the correct X-BFF-Key header.
    Add to the FastAPI app in main.py.
    """

    def __init__(self, app, secret_key: str) -> None:
        super().__init__(app)
        self._secret = secret_key
        if not secret_key or secret_key in ("", "change-me-to-a-long-random-string"):
            logger.warning(
                "BFF_SECRET_KEY is not set or is the default value — "
                "BFF auth middleware is running in PERMISSIVE mode (dev only)",
                extra={"phase": "startup"},
            )
            self._permissive = True
        else:
            self._permissive = False
            logger.info("BFF auth middleware active", extra={"phase": "startup"})

    async def dispatch(self, request: Request, call_next) -> Response:
        # Always allow exempt paths and OPTIONS preflight
        if request.url.path in _EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Permissive mode (dev) — skip check but warn
        if self._permissive:
            return await call_next(request)

        key = request.headers.get("X-BFF-Key", "")
        if key != self._secret:
            logger.warning(
                "BFF auth rejected: missing or wrong X-BFF-Key from %s %s",
                request.method, request.url.path,
                extra={
                    "request_id": request.headers.get("X-Request-ID", "unknown"),
                    "client_ip":  request.client.host if request.client else "unknown",
                },
            )
            return Response(
                content='{"error":"forbidden","message":"Direct backend access is not allowed"}',
                status_code=403,
                media_type="application/json",
            )

        return await call_next(request)
