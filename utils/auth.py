"""Authentication helpers for the router's management and data planes.

The router serves both planes on a single listening port:

* the **management plane** (``/add_workers``, ``/remove_workers``,
  ``/list_workers``, ``/pretty_print_tree``) mutates the routing topology and
  dumps internal state.  It must never be reachable anonymously, therefore it
  *fails closed*: requests are rejected unless an admin token is configured
  (or the operator explicitly opts out with ``--allow-anonymous-admin``).
* the **data plane** (``/v1/completions``) carries the inference traffic.  It
  supports optional token authentication via ``--api-token``.

Tokens can be supplied either through the command line or through the
environment (``VLLM_ROUTER_ADMIN_TOKEN`` / ``VLLM_ROUTER_API_TOKEN``), so that
secrets do not have to appear in the process list.
"""

import hmac
import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN_ENV = "VLLM_ROUTER_ADMIN_TOKEN"
API_TOKEN_ENV = "VLLM_ROUTER_API_TOKEN"

logger = logging.getLogger("router")


def resolve_token(explicit: str, env_name: str) -> str:
    """Return the configured token, falling back to the environment variable."""
    if explicit:
        return explicit.strip()
    return os.environ.get(env_name, "").strip()


def extract_token(request: Request) -> str:
    """Extract the caller's token from the request headers.

    Both ``Authorization: Bearer <token>`` and the explicit ``X-Admin-Token`` /
    ``X-API-Token`` headers are accepted.
    """
    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials.strip():
            return credentials.strip()

    for header in ("X-Admin-Token", "X-API-Token"):
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip()

    return ""


def token_matches(provided: str, expected: str) -> bool:
    """Constant time comparison of the presented and the expected token."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "-"


def require_admin(request: Request) -> str:
    """FastAPI dependency that guards the management plane (CWE-862)."""
    if getattr(request.app.state, "allow_anonymous_admin", False):
        return "anonymous"

    expected = getattr(request.app.state, "admin_token", "")
    if not expected:
        logger.error(
            "Rejected management request %s %s: no admin token is configured (start with --admin-token or set %s)",
            request.method,
            request.url.path,
            ADMIN_TOKEN_ENV,
        )
        raise HTTPException(status_code=403, detail="Management API is disabled because no admin token is configured")

    provided = extract_token(request)
    if not token_matches(provided, expected):
        logger.warning(
            "Rejected unauthenticated management request %s %s from %s",
            request.method,
            request.url.path,
            _client_host(request),
        )
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")

    return "admin"


def require_api_token(request: Request) -> str:
    """FastAPI dependency that optionally guards the data plane."""
    expected = getattr(request.app.state, "api_token", "")
    if not expected:
        return "anonymous"

    provided = extract_token(request)
    if not token_matches(provided, expected):
        logger.warning(
            "Rejected unauthenticated inference request %s %s from %s",
            request.method,
            request.url.path,
            _client_host(request),
        )
        raise HTTPException(status_code=401, detail="Invalid or missing API token")

    return "api"
