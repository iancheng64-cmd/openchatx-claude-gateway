"""Typed application state.

FastAPI/Starlette's ``app.state`` (and ``request.state``) is a bare
``State`` object -- attributes are stashed in a dict, so ``request.app.state.foo``
gives neither type checking nor "go to definition" in an IDE. ``GatewayState``
declares every attribute the app actually stores (set once in
``create_app``), and :func:`get_state` hands back a properly typed view of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from starlette.datastructures import State

if TYPE_CHECKING:
    from fastmcp import Client
    from starlette.requests import Request

    from mcp_gateway.config import GatewayConfig
    from mcp_gateway.oauth_server import GatewayOAuthProvider
    from mcp_gateway.ratelimit import RateLimiter
    from mcp_gateway.storage import Storage
    from mcp_gateway.upstream import BackendManager
    from mcp_gateway.users import SessionManager


class GatewayState(State):
    """Attributes stored on ``app.state`` by :func:`mcp_gateway.app.create_app`."""

    config: GatewayConfig
    storage: Storage
    oauth_provider: GatewayOAuthProvider
    backend_manager: BackendManager
    backend_clients: dict[str, Client]
    sessions: SessionManager
    login_limiter: RateLimiter
    register_limiter: RateLimiter


def get_state(request: Request) -> GatewayState:
    """Return ``request.app.state`` typed as :class:`GatewayState`.

    The object at runtime is unchanged (still the ``State`` instance FastAPI
    manages) -- this only narrows the static type so attribute access is
    checked and IDE-navigable instead of resolving to ``Any``.
    """
    return cast("GatewayState", request.app.state)
