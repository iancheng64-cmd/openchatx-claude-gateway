"""Upstream (gateway -> backend MCP server) authentication and clients.

Supported backend auth methods:

- ``none``    – public servers (e.g. Microsoft Learn docs MCP)
- ``bearer``  – static token injected as ``Authorization: Bearer ...``
- ``headers`` – arbitrary static headers (API keys etc.)
- ``oauth``   – full OAuth 2.1 client per the MCP authorization spec, using
  the official SDK's ``OAuthClientProvider``: protected-resource/AS metadata
  discovery, CIMD (URL-based client ID hosted by the gateway) when the
  upstream AS advertises support, Dynamic Client Registration as fallback,
  PKCE, resource indicators, and automatic token refresh.

OAuth backends are connected interactively once: an admin opens
``/oauth/connect/<backend>`` in a browser (behind gateway login), gets
redirected to the upstream authorization server, and the callback lands on
``/oauth/callback``. Tokens are persisted encrypted in SQLite; afterwards the
flow is fully automatic (refresh included). MCP traffic never triggers an
interactive flow on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.client.transports.base import TransportOptions
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl

from mcp_gateway.config import BackendConfig, GatewayConfig
from mcp_gateway.storage import Storage

logger = logging.getLogger(__name__)

CONNECT_FLOW_TIMEOUT_SECONDS = 300

# Path (relative to the public URL) where the gateway hosts its own Client ID
# Metadata Document for CIMD-capable upstream authorization servers.
CLIENT_METADATA_PATH = "/oauth/client-metadata.json"
UPSTREAM_CALLBACK_PATH = "/oauth/callback"


class NoForwardStreamableHttpTransport(StreamableHttpTransport):
    """Streamable HTTP transport that never forwards the caller's HTTP headers.

    FastMCP proxies connect their backend client with
    ``TransportOptions(forward_incoming_headers=True)``, which copies the
    inbound request's headers -- including ``Authorization`` -- onto the
    backend request. That is token passthrough, which the MCP authorization
    spec forbids: the gateway token issued to an MCP client must never reach a
    backend. The proxy re-applies that option to a fresh copy of the client on
    every request, so it can't be switched off from the outside; this
    transport overrides it at the one place it takes effect instead. Backends
    only ever see credentials the gateway itself holds (static headers or its
    own upstream OAuth tokens).

    ``gateway.build_gateway`` refuses to start unless every backend client uses
    this transport and ``TransportOptions`` still has the field overridden
    here.
    """

    @contextlib.asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **session_kwargs: Any
    ) -> AsyncIterator[ClientSession]:
        options = replace(transport_options or TransportOptions(), forward_incoming_headers=False)
        async with super().connect_session(
            transport_options=options, **session_kwargs
        ) as session:
            yield session


async def probe_backend(client: Client) -> str:
    """Prove a connected `client`'s backend is live; returns a short detail.

    ``ping`` only exists in the handshake-era protocol: the sessionless
    ``2026-07-28`` era dropped it along with the rest of the lifecycle
    methods, and a modern backend answers it with "Method not found". On a
    modern connection the ``server/discover`` round trip that opened it
    already proved the backend answers, so there is nothing more to send.
    """
    if client.initialize_result is None:
        return f"Connected (protocol {client.protocol_version})"
    await client.ping()
    return f"Ping succeeded (protocol {client.protocol_version})"


class NotConnectedError(Exception):
    """OAuth backend has no stored tokens and no interactive flow is running."""


class JsonTokenOAuthClientProvider(OAuthClientProvider):
    """OAuthClientProvider that always asks token endpoints for a JSON response.

    Some authorization servers (notably GitHub's) reply to token requests with
    ``application/x-www-form-urlencoded`` unless the client explicitly sends
    ``Accept: application/json``. The SDK only ever parses JSON, so without
    this header those responses fail to parse even though the exchange itself
    succeeded (an ``access_token=...&token_type=bearer`` body, not an error).
    """

    async def _exchange_token_authorization_code(self, *args: Any, **kwargs: Any):
        request = await super()._exchange_token_authorization_code(*args, **kwargs)
        request.headers["accept"] = "application/json"
        return request

    async def _refresh_token(self, *args: Any, **kwargs: Any):
        request = await super()._refresh_token(*args, **kwargs)
        request.headers["accept"] = "application/json"
        return request

    async def _initialize(self) -> None:
        """Restore absolute token expiry across process restarts.

        The SDK's ``OAuthContext.token_expiry_time`` -- the timestamp
        ``is_token_valid()`` actually checks -- lives in memory only. It's
        computed when tokens are (re)issued and never persisted; ``_initialize()``
        (called lazily on first use of a fresh ``OAuthClientProvider``, i.e.
        after every process restart) only reloads ``current_tokens`` and
        ``client_info`` from storage, leaving ``token_expiry_time`` at its
        default of ``None``.

        ``is_token_valid()`` treats ``None`` as "no known expiry -- still
        valid", so after a restart a genuinely expired access token gets sent
        as-is. The upstream server then returns 401, and the SDK's 401 handler
        goes straight to a full *interactive* re-authorization -- it never
        tries the ``refresh_token`` grant -- so the flow dies with
        ``NotConnectedError`` even though a perfectly good, unexpired refresh
        token is sitting in storage.

        ``DbTokenStorage.get_tokens`` already decays the stored ``expires_in``
        to the token's remaining lifetime, so the SDK's own
        ``update_token_expiry`` helper is enough to reconstruct
        ``token_expiry_time`` here -- no separate bookkeeping needed. This
        override still has to exist: ``_initialize`` is the only place stored
        tokens enter the context, and the SDK deliberately doesn't call
        ``update_token_expiry`` there itself. Tokens with no ``expires_in``
        (e.g. classic GitHub OAuth App tokens, which don't expire) are left
        with no expiry, same as upstream default behaviour.
        """
        await super()._initialize()
        if self.context.current_tokens is not None:
            self.context.update_token_expiry(self.context.current_tokens)


class DbTokenStorage(TokenStorage):
    """SDK TokenStorage backed by the gateway's encrypted SQLite store.

    When the backend has statically configured OAuth client credentials
    (``auth.client_id``, for upstream authorization servers that support
    neither CIMD nor Dynamic Client Registration, e.g. GitHub), those are
    returned as-is and never overwritten by a CIMD/DCR result.
    """

    def __init__(self, storage: Storage, backend: str, static_client_info: OAuthClientInformationFull | None = None):
        self._storage = storage
        self._backend = backend
        self._static_client_info = static_client_info

    async def get_tokens(self) -> OAuthToken | None:
        data = self._storage.get_upstream(self._backend, "tokens")
        if not data:
            return None
        data = dict(data)
        expires_at = data.pop("expires_at", None)
        if expires_at is not None:
            # expires_in is relative to when the token was received; decay it
            # to the remaining lifetime (negative once past expiry -- that's
            # fine, calculate_token_expiry just produces a past timestamp).
            data["expires_in"] = int(expires_at - time.time())
        return OAuthToken.model_validate(data)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = tokens.model_dump(mode="json")
        if tokens.expires_in is not None:
            data["expires_at"] = time.time() + tokens.expires_in
        self._storage.save_upstream(self._backend, "tokens", data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if self._static_client_info is not None:
            return self._static_client_info
        data = self._storage.get_upstream(self._backend, "client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        if self._static_client_info is not None:
            return
        self._storage.save_upstream(
            self._backend, "client_info", client_info.model_dump(mode="json")
        )


class _ForcedChallengeAuth(httpx2.Auth):
    """Wraps an OAuthClientProvider so its interactive path always runs.

    ``async_auth_flow`` only takes the interactive path when a request comes
    back 401 -- but some backends (observed with GitHub's Copilot MCP server)
    happily answer basic protocol methods like ``initialize``/``ping``
    unauthenticated and only enforce auth on tool calls. For those, no probe
    ever 401s, ``redirect_handler`` never runs, and a connect attempt driven
    by plain ``client.ping()`` waits out ``start_connect``'s timeout without
    ever producing an authorize URL.

    Relays every request/response pair between ``httpx2.AsyncClient`` and the
    provider's own generator untouched, with two exceptions:

    - the probe's response is replaced by a synthetic 401 unless the backend
      really did challenge (a real 401 is passed through so its
      ``WWW-Authenticate`` header -- resource metadata URL, scope hint -- is
      used exactly as ``async_auth_flow`` would use it);
    - the flow's last step, replaying the probe with the freshly issued
      token, is dropped: nothing here reads that response, and a GET against
      a streamable-HTTP endpoint can block on an open SSE stream.

    Everything else -- generator lifecycle (``httpx2`` closes it in a
    ``finally`` regardless of how the flow ends), redirects, timeouts,
    proxy/TLS config, response reads -- is handled by ``httpx2.AsyncClient``
    itself, the same as for any real request the provider would ever see.
    """

    requires_response_body = True

    def __init__(self, provider: OAuthClientProvider) -> None:
        self._provider = provider

    async def async_auth_flow(self, request: httpx2.Request):
        inner = self._provider.async_auth_flow(request)
        try:
            outgoing = await inner.__anext__()
            probe_sent = False
            while True:
                # async_auth_flow yields the *same* request object it was
                # given for the probe and, after the token exchange, for the
                # authenticated replay. Everything else (refresh, discovery,
                # registration, token exchange) is a request it built itself.
                is_probe = outgoing is request
                if is_probe and probe_sent:
                    return
                response = yield outgoing
                if is_probe:
                    probe_sent = True
                    if response.status_code != 401:
                        response = httpx2.Response(401, request=outgoing)
                try:
                    outgoing = await inner.asend(response)
                except StopAsyncIteration:
                    return
        finally:
            await inner.aclose()


async def _drive_interactive_reauth(provider: OAuthClientProvider) -> None:
    """Force a fresh interactive authorization for `provider`."""
    async with httpx2.AsyncClient(
        timeout=30.0, follow_redirects=True, auth=_ForcedChallengeAuth(provider)
    ) as http:
        await http.get(provider.context.server_url)


class ConnectFlow:
    """State for one interactive backend authorization flow."""

    def __init__(self, backend: str):
        self.backend = backend
        self.authorize_url: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self.callback: asyncio.Future[AuthorizationCodeResult] = (
            asyncio.get_event_loop().create_future()
        )
        self.done: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()
        self.state: str | None = None
        self.created_at = time.time()
        # The background task driving this flow (set once start_connect creates
        # it). Must be cancelled before abandoning the flow: the OAuthClientProvider
        # is cached per backend and its internal lock is held for the task's whole
        # lifetime, so an orphaned task blocks every later connect attempt on that
        # same lock until it naturally times out (up to CONNECT_FLOW_TIMEOUT_SECONDS).
        self.task: asyncio.Task[None] | None = None


class BackendManager:
    """Builds authenticated FastMCP clients for every configured backend and
    orchestrates interactive OAuth connect flows."""

    def __init__(self, config: GatewayConfig, storage: Storage):
        self.config = config
        self.storage = storage
        self._flows_by_backend: dict[str, ConnectFlow] = {}
        self._flows_by_state: dict[str, ConnectFlow] = {}
        self._oauth_providers: dict[str, OAuthClientProvider] = {}

    # ------------------------------------------------------------- client building

    def build_client(self, name: str, backend: BackendConfig) -> Client:
        # Lowercase header names so they take precedence in any case-sensitive
        # merge with per-request headers further down the stack.
        headers = {k.lower(): v for k, v in backend.headers.items()}
        auth: Any = None
        if backend.auth.type == "bearer":
            headers["authorization"] = f"Bearer {backend.auth.token}"
        elif backend.auth.type == "headers":
            headers.update({k.lower(): v for k, v in backend.auth.headers.items()})
        elif backend.auth.type == "oauth":
            auth = self._build_oauth_provider(name, backend)
        logger.debug(
            "Built client for backend %s (auth=%s, %d static header(s))",
            name,
            backend.auth.type,
            len(headers),
        )
        transport = NoForwardStreamableHttpTransport(
            backend.url, headers=headers or None, auth=auth
        )
        return Client(transport)

    def _build_oauth_provider(self, name: str, backend: BackendConfig) -> OAuthClientProvider:
        if name in self._oauth_providers:
            return self._oauth_providers[name]

        public_url = self.config.server.public_url
        redirect_uri = f"{public_url}{UPSTREAM_CALLBACK_PATH}"
        scopes = " ".join(backend.auth.scopes) if backend.auth.scopes else None

        static_client_info: OAuthClientInformationFull | None = None
        if backend.auth.client_id:
            # Pre-registered client (e.g. a GitHub OAuth App): the upstream AS
            # supports neither CIMD nor DCR, so use these credentials directly
            # and skip registration entirely.
            static_client_info = OAuthClientInformationFull(
                client_id=backend.auth.client_id,
                client_secret=backend.auth.client_secret,
                redirect_uris=[AnyUrl(redirect_uri)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method=(
                    "client_secret_post" if backend.auth.client_secret else "none"
                ),
                scope=scopes,
            )

        client_metadata = OAuthClientMetadata(
            client_name="MCP Gateway",
            redirect_uris=[AnyUrl(redirect_uri)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=scopes,
        )

        # CIMD: use the gateway's own hosted client metadata document as the
        # client_id when the upstream AS advertises support for it. The SDK
        # falls back to Dynamic Client Registration automatically otherwise.
        client_metadata_url: str | None = None
        if (
            static_client_info is None
            and not backend.auth.prefer_dcr
            and public_url.startswith("https://")
        ):
            client_metadata_url = f"{public_url}{CLIENT_METADATA_PATH}"

        provider = JsonTokenOAuthClientProvider(
            server_url=backend.url,
            client_metadata=client_metadata,
            storage=DbTokenStorage(self.storage, name, static_client_info),
            redirect_handler=self._make_redirect_handler(name),
            callback_handler=self._make_callback_handler(name),
            client_metadata_url=client_metadata_url,
        )
        self._oauth_providers[name] = provider
        logger.debug(
            "Built OAuth provider for backend %s (registration=%s)",
            name,
            "static" if static_client_info else ("cimd" if client_metadata_url else "dcr"),
        )
        return provider

    def client_metadata_document(self) -> dict[str, Any]:
        """The CIMD document the gateway hosts for upstream authorization servers."""
        public_url = self.config.server.public_url
        return {
            "client_id": f"{public_url}{CLIENT_METADATA_PATH}",
            "client_name": "MCP Gateway",
            "client_uri": public_url,
            "redirect_uris": [f"{public_url}{UPSTREAM_CALLBACK_PATH}"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    # ------------------------------------------------------------- connect flow

    def _make_redirect_handler(self, backend: str):
        async def redirect_handler(authorization_url: str) -> None:
            flow = self._flows_by_backend.get(backend)
            if flow is None or flow.authorize_url.done():
                # OAuth flow triggered outside an interactive connect session
                # (e.g. plain MCP traffic hitting an unconnected backend).
                raise NotConnectedError(
                    f"Backend '{backend}' requires authorization. "
                    f"Open {self.config.server.public_url}/ui/backends to connect it."
                )
            state = parse_qs(urlparse(authorization_url).query).get("state", [None])[0]
            flow.state = state
            if state is not None:
                self._flows_by_state[state] = flow
            logger.info("Backend %s: redirecting to upstream authorization server", backend)
            flow.authorize_url.set_result(authorization_url)

        return redirect_handler

    def _make_callback_handler(self, backend: str):
        async def callback_handler() -> AuthorizationCodeResult:
            flow = self._flows_by_backend.get(backend)
            if flow is None:
                raise NotConnectedError(f"No active connect flow for backend '{backend}'")
            return await asyncio.wait_for(flow.callback, timeout=CONNECT_FLOW_TIMEOUT_SECONDS)

        return callback_handler

    async def start_connect(self, name: str, client: Client) -> str:
        """Start an interactive OAuth flow; returns the upstream authorization URL.

        Drives the SDK auth machinery in a background task via
        ``_drive_interactive_reauth``: forcing the discovery -> registration
        -> redirect path unconditionally (see there for why this can't just
        wait for a real 401), through the redirect handler that hands the
        authorization URL back to this coroutine.
        """
        logger.info("Backend %s: starting interactive OAuth connect flow", name)
        backend = self.config.backends.get(name)
        if backend is None or backend.auth.type != "oauth":
            raise ValueError(f"Backend '{name}' is not an OAuth backend")

        old_flow = self._flows_by_backend.pop(name, None)
        if old_flow is not None:
            if old_flow.state:
                self._flows_by_state.pop(old_flow.state, None)
            if old_flow.task is not None and not old_flow.task.done():
                # The old flow's background task holds this backend's OAuth
                # provider lock for its whole lifetime (up to
                # CONNECT_FLOW_TIMEOUT_SECONDS). Cancel and wait for it to
                # actually unwind before starting a new attempt, or the new
                # attempt would silently queue behind that lock and time out
                # without ever sending a request.
                logger.debug("Backend %s: cancelling stale connect flow task", name)
                old_flow.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await old_flow.task

        flow = ConnectFlow(name)
        self._flows_by_backend[name] = flow
        provider = self._build_oauth_provider(name, backend)

        async def drive() -> None:
            # Force a fresh authorization via _drive_interactive_reauth
            # instead of just pinging and hoping the server 401s an
            # unauthenticated/expired token: JsonTokenOAuthClientProvider now
            # reconstructs expiry correctly and proactively refreshes, and
            # some backends never 401 basic protocol methods at all -- either
            # way a plain client.ping() can succeed without ever reaching the
            # redirect_handler, leaving authorize_url unresolved until this
            # whole connect attempt times out.
            try:
                await _drive_interactive_reauth(provider)
                # Confirm the freshly-obtained token actually works end to
                # end against the real backend.
                async with client:
                    await probe_backend(client)
                logger.info("Backend %s: OAuth connect flow succeeded", name)
                flow.done.set_result(None)
            except Exception as exc:  # noqa: BLE001 - report to the waiting UI
                logger.warning("Backend %s: OAuth connect flow failed: %s", name, exc)
                if not flow.done.done():
                    flow.done.set_result(f"{type(exc).__name__}: {exc}")
                if not flow.authorize_url.done():
                    flow.authorize_url.set_exception(
                        RuntimeError(f"Authorization failed: {exc}")
                    )

        flow.task = asyncio.create_task(drive())
        try:
            return await asyncio.wait_for(flow.authorize_url, timeout=60)
        except TimeoutError:
            flow.task.cancel()
            raise TimeoutError(
                f"Backend '{name}' did not redirect to an authorization endpoint within 60s"
            ) from None

    def deliver_callback(
        self, code: str, state: str | None, iss: str | None = None
    ) -> str | None:
        """Route an upstream authorization callback to the waiting flow.

        Returns the backend name the callback was delivered to, or None.

        Only an exact ``state`` match resolves a flow. There used to be a
        fallback that guessed "the single active flow" whenever ``state`` was
        absent or didn't match, on the theory that some non-conformant
        servers drop it. That fallback let *any* request reach a pending
        flow's one-shot callback future — including an anonymous request with
        a forged ``state`` — permanently destroying an admin's in-progress
        connect attempt (PKCE and the MCP SDK's own state check still stop
        the forged code from ever being exchanged, but the legitimate flow is
        consumed and has to be restarted). A request that fails to match is
        now simply ignored, leaving any real pending flow untouched.

        ``iss`` is the RFC 9207 authorization-response issuer, if the upstream
        AS sent one; the SDK checks it against the discovered AS metadata.
        """
        flow: ConnectFlow | None = self._flows_by_state.get(state) if state is not None else None
        if flow is None or flow.callback.done():
            logger.warning("Received upstream OAuth callback with no matching connect flow")
            return None
        self._flows_by_state.pop(state, None)
        logger.debug("Backend %s: delivering upstream OAuth callback", flow.backend)
        flow.callback.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))
        return flow.backend

    async def wait_connect_result(self, name: str) -> str | None:
        """Wait for a running connect flow to finish; returns an error or None."""
        flow = self._flows_by_backend.get(name)
        if flow is None:
            return "No active connect flow"
        try:
            result = await asyncio.wait_for(flow.done, timeout=CONNECT_FLOW_TIMEOUT_SECONDS)
        except TimeoutError:
            result = "Timed out waiting for authorization"
            if flow.task is not None:
                flow.task.cancel()
        finally:
            self._flows_by_backend.pop(name, None)
            if flow.state:
                self._flows_by_state.pop(flow.state, None)
        return result

    def disconnect(self, name: str) -> None:
        """Drop stored upstream credentials for a backend."""
        logger.info("Backend %s: disconnecting (dropping stored upstream credentials)", name)
        self.storage.delete_upstream(name)
        provider = self._oauth_providers.get(name)
        if provider is not None:
            provider.context.clear_tokens()
            provider.context.client_info = None
            provider._initialized = False

    # ------------------------------------------------------------- status

    def backend_status(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, backend in self.config.backends.items():
            entry: dict[str, Any] = {
                "name": name,
                "url": backend.url,
                "enabled": backend.enabled,
                "auth_type": backend.auth.type,
            }
            if backend.auth.type == "oauth":
                tokens = self.storage.get_upstream(name, "tokens")
                client_info = self.storage.get_upstream(name, "client_info")
                entry["connected"] = tokens is not None
                entry["has_refresh_token"] = bool(tokens and tokens.get("refresh_token"))
                if backend.auth.client_id:
                    entry["registration"] = "static"
                elif client_info and str(client_info.get("client_id", "")).startswith("https://"):
                    entry["registration"] = "cimd"
                elif client_info:
                    entry["registration"] = "dcr"
                else:
                    entry["registration"] = None
            else:
                entry["connected"] = True
            out.append(entry)
        return out

    # ------------------------------------------------------------- connection test

    async def _check_auth(self, name: str, backend: BackendConfig) -> str:
        """Local, no-network check of whatever auth material is configured.

        This doesn't by itself prove the backend *accepts* the credentials --
        that's what the list_tools check (a real authenticated request) is
        for -- only that the gateway actually holds something to send.
        """
        if backend.auth.type == "none":
            return "No authentication configured"
        if backend.auth.type == "oauth":
            tokens = self.storage.get_upstream(name, "tokens")
            if tokens is None:
                raise NotConnectedError(
                    f"Backend '{name}' has no stored OAuth tokens -- connect it first"
                )
            has_refresh = bool(tokens.get("refresh_token"))
            return "OAuth tokens present" + (
                " (refresh token available)" if has_refresh else " (no refresh token)"
            )
        if backend.auth.type == "bearer":
            return "Static bearer token configured"
        return "Static headers configured"

    async def test_connection(self, name: str, client: Client) -> AsyncIterator[dict[str, Any]]:
        """Run a live connection test against `name`, yielding progress events.

        Each check yields a ``running`` event followed by an ``ok``/``error``
        event, so a caller can stream live progress to a UI. Stops at the
        first failing check -- there is no point listing tools on a backend
        that couldn't even be pinged.
        """
        backend = self.config.backends[name]
        # Set as soon as any check has reported a definitive result, so a
        # cleanup-time exception from __aexit__ (e.g. closing the transport
        # after a check already failed, or even after everything succeeded)
        # is never mistaken for a fresh ping failure below.
        reported = False

        yield {"check": "ping", "status": "running"}
        try:
            async with client:
                try:
                    detail = await probe_backend(client)
                except Exception as exc:  # noqa: BLE001 - report to the waiting UI
                    reported = True
                    yield {
                        "check": "ping",
                        "status": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                    return
                yield {"check": "ping", "status": "ok", "detail": detail}

                yield {"check": "auth", "status": "running"}
                try:
                    detail = await self._check_auth(name, backend)
                except Exception as exc:  # noqa: BLE001 - report to the waiting UI
                    reported = True
                    yield {
                        "check": "auth",
                        "status": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                    return
                yield {"check": "auth", "status": "ok", "detail": detail}

                yield {"check": "list_tools", "status": "running"}
                try:
                    tools = await client.list_tools()
                except Exception as exc:  # noqa: BLE001 - report to the waiting UI
                    reported = True
                    yield {
                        "check": "list_tools",
                        "status": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                    return
                reported = True
                yield {
                    "check": "list_tools",
                    "status": "ok",
                    "detail": f"Listed {len(tools)} tool(s)",
                }
        except Exception as exc:  # noqa: BLE001 - report to the waiting UI
            if reported:
                logger.debug(
                    "Backend %s: ignoring cleanup error after connection test finished: %s",
                    name,
                    exc,
                )
                return
            # Opening the client session itself failed (transport couldn't
            # even connect) -- no check body above ran, so attribute it to
            # ping, the check this replaces.
            logger.info("Backend %s: connection test failed to open session: %s", name, exc)
            yield {"check": "ping", "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
