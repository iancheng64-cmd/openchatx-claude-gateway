"""The aggregating FastMCP server.

Each enabled backend is mounted as a live proxy under its configured name, so
its tools/resources/prompts appear namespaced (e.g. tool ``create_issue`` of
backend ``github`` becomes ``github_create_issue``). Requests are forwarded to
the backend in real time with the gateway's own upstream credentials; a
backend that is down or not yet connected only removes its own tools from the
listing instead of breaking the whole gateway.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import logging

from fastmcp import Client, FastMCP
from fastmcp.client.transports.base import TransportOptions
from fastmcp.server import create_proxy
from mcp.types import Icon, ToolAnnotations

from mcp_gateway.config import GatewayConfig
from mcp_gateway.oauth_server import GatewayOAuthProvider
from mcp_gateway.upstream import BackendManager, NoForwardStreamableHttpTransport
from mcp_gateway.web import STATIC_DIR

logger = logging.getLogger(__name__)

# Single source of truth for the gateway's mark: ui/public/favicon.svg,
# copied verbatim into the built UI's static root by `npm run build` (see
# ui/index.html, which points its own <link rel="icon"> at the same file).
# Read it from there at runtime and re-encode as a data URI so MCP clients
# (e.g. connector pickers) get the identical icon via the initialize
# response's serverInfo.icons -- no second copy of the SVG to keep in sync.
_FAVICON_PATH = STATIC_DIR / "favicon.svg"


@functools.lru_cache(maxsize=1)
def _server_icons() -> list[Icon]:
    try:
        svg = _FAVICON_PATH.read_bytes()
    except OSError:
        # UI not built yet (e.g. local dev, tests) -- omit the icon rather
        # than failing gateway startup; see web.py's handling of the same
        # missing-build case for /ui/.
        logger.debug("Favicon not found at %s; MCP server will advertise no icon", _FAVICON_PATH)
        return []
    data_uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    return [Icon(src=data_uri, mime_type="image/svg+xml")]


def _require_no_header_forwarding(name: str, client: Client) -> None:
    """Refuse to start unless `client` can't pass the caller's token upstream.

    FastMCP proxies forward the inbound request's headers (Authorization
    included) to the backend by default. That is token passthrough, which the
    MCP authorization spec explicitly forbids, and the single most important
    invariant in the gateway. ``NoForwardStreamableHttpTransport`` turns it
    off by overriding ``TransportOptions.forward_incoming_headers``; fail
    startup loudly if a backend client bypasses that transport, or if a future
    fastmcp release renames or removes the option -- either would re-enable
    passthrough (or break every backend call) with no error at startup.
    """
    transport = getattr(client, "transport", None)
    if not isinstance(transport, NoForwardStreamableHttpTransport):
        raise TypeError(
            f"Backend {name!r}: transport {type(transport).__name__} is not "
            "NoForwardStreamableHttpTransport; cannot guarantee the "
            "no-token-passthrough invariant."
        )
    if "forward_incoming_headers" not in {f.name for f in dataclasses.fields(TransportOptions)}:
        raise RuntimeError(
            "fastmcp's TransportOptions has no 'forward_incoming_headers' field; "
            "cannot guarantee the no-token-passthrough invariant. This likely "
            "means the installed fastmcp version changed its proxy transport API."
        )


def build_gateway(
    config: GatewayConfig,
    provider: GatewayOAuthProvider,
    manager: BackendManager,
    clients: dict[str, Client],
) -> FastMCP:
    mcp: FastMCP = FastMCP(
        name="MCP Gateway",
        instructions=(
            "Aggregating gateway in front of multiple backend MCP servers. "
            "Tools are namespaced by backend name (e.g. github_create_issue). "
            "Use the gateway_status tool to inspect configured backends."
        ),
        icons=_server_icons(),
        auth=provider,
        # Don't leak backend URLs, HTTP error bodies or internal exception
        # types to MCP clients; diagnose failures from the server logs
        # instead (run with --log-level debug for detail).
        mask_error_details=True,
    )

    # Status icon: simple circle with dot indicator
    _status_icon_svg = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        '<circle cx="12" cy="12" r="11" stroke="currentColor" stroke-width="2" fill="none"/>'
        '<circle cx="12" cy="12" r="5" fill="currentColor"/>'
        '</svg>'
    )
    _status_icon_data_uri = (
        "data:image/svg+xml;base64," +
        base64.b64encode(_status_icon_svg.encode("utf-8")).decode("ascii")
    )

    @mcp.tool(
        name="gateway_status",
        annotations=ToolAnnotations(
            title="Gateway Status",
            read_only_hint=True,
            open_world_hint=False
        ),
        icons=[Icon(src=_status_icon_data_uri, mime_type="image/svg+xml")]
    )
    def gateway_status() -> list[dict]:
        """List the backends configured in this gateway and their connection state."""
        logger.debug("gateway_status tool invoked")
        return manager.backend_status()

    for name, backend in config.backends.items():
        if not backend.enabled:
            logger.info("Backend %s is disabled; skipping", name)
            continue
        _require_no_header_forwarding(name, clients[name])
        proxy = create_proxy(clients[name], name=f"proxy-{name}", mask_error_details=True)
        mcp.mount(proxy, namespace=name)
        logger.info("Mounted backend %s (%s, auth=%s)", name, backend.url, backend.auth.type)

    return mcp
