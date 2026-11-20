"""Authentication helpers for the router's management and data planes.

The router serves both planes on a single listening port:

* the **management plane** (``/add_workers``, ``/remove_workers``,
  ``/list_workers``, ``/pretty_print_tree``) mutates the routing topology and
  dumps internal state.
* the **data plane** (``/v1/completions``) carries the inference traffic.

The authentication is **opt in**. When no token is configured the endpoints
behave exactly like they did before, they are simply not protected. When a
token is configured, every request that does not carry it is rejected.

The switch is deliberately explicit: a router that is deployed without a
token keeps working, and a router that is deployed with one cannot be
reached without it. Failing closed by default would break every existing
deployment, because the management endpoints were reachable without a token
before.

Tokens can be supplied either through the command line (``--admin-token`` /
``--api-token``) or through the environment (``VLLM_ROUTER_ADMIN_TOKEN`` /
``VLLM_ROUTER_API_TOKEN``), so that secrets do not have to appear in the
process list.
"""

import hmac
import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN_ENV = "VLLM_ROUTER_ADMIN_TOKEN"
API_TOKEN_ENV = "VLLM_ROUTER_API_TOKEN"

# A token that is shorter than this is easy to guess. The router only warns
# about it, it does not reject the token: the operator decides.
MIN_TOKEN_LENGTH = 16

logger = logging.getLogger("router")

# Number of rejected management / data plane requests. A rising counter is the
# only signal that a client is guessing tokens, therefore it is kept and
# reported with every rejection.
_REJECTED_REQUESTS = {"admin": 0, "api": 0}


def rejected_request_count(plane: str = "admin") -> int:
    """Return the number of rejected requests of the given plane."""
    return _REJECTED_REQUESTS.get(plane, 0)


def resolve_token(explicit: str, env_name: str) -> str:
    """Return the configured token, falling back to the environment variable."""
    token = (explicit or "").strip()
    if not token:
        token = os.environ.get(env_name, "").strip()

    if token and len(token) < MIN_TOKEN_LENGTH:
        logger.warning(
            "The token of %s is shorter than %s characters, it is easy to guess",
            env_name,
            MIN_TOKEN_LENGTH,
        )

    return token


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
    """Constant time comparison of the presented and the expected token.

    The comparison is done on the UTF-8 bytes: ``hmac.compare_digest`` refuses
    ``str`` objects that contain non-ASCII characters and would raise a
    ``TypeError`` instead of reporting a mismatch, which would turn a wrong
    token into a 500 instead of a 401.
    """
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "-"


def require_admin(request: Request) -> str:
    """FastAPI dependency that guards the management plane (CWE-862).

    The dependency only rejects a request when an admin token is configured.
    Without a token the management plane is not protected, exactly like it
    was before this dependency existed.
    """
    expected = getattr(request.app.state, "admin_token", "")
    if not expected:
        # The protection is switched off, there is no token to check.
        return "anonymous"

    provided = extract_token(request)
    if not token_matches(provided, expected):
        _REJECTED_REQUESTS["admin"] += 1
        logger.warning(
            "Rejected unauthenticated management request %s %s from %s (total rejections: %s)",
            request.method,
            request.url.path,
            _client_host(request),
            _REJECTED_REQUESTS["admin"],
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
        _REJECTED_REQUESTS["api"] += 1
        logger.warning(
            "Rejected unauthenticated inference request %s %s from %s (total rejections: %s)",
            request.method,
            request.url.path,
            _client_host(request),
            _REJECTED_REQUESTS["api"],
        )
        raise HTTPException(status_code=401, detail="Invalid or missing API token")

    return "api"
