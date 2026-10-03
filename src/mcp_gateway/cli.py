"""Command-line entry point: `mcp-gateway run` / `mcp-gateway hash-password`."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-gateway", description="Self-hosted MCP gateway")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the gateway server")
    run.add_argument(
        "-c",
        "--config",
        default=os.environ.get("MCP_GATEWAY_CONFIG", "config.yaml"),
        help="Path to the YAML config file (default: config.yaml or $MCP_GATEWAY_CONFIG)",
    )
    run.add_argument(
        "--log-level",
        default=os.environ.get("MCP_GATEWAY_LOG_LEVEL", "info"),
        help=(
            "Log verbosity: debug, info, warning, error, critical "
            "(default: info or $MCP_GATEWAY_LOG_LEVEL). Also governs FastMCP's "
            "own internal logging."
        ),
    )

    sub.add_parser("hash-password", help="Generate a bcrypt hash for the users section")

    check = sub.add_parser("check", help="Validate the config file and exit")
    check.add_argument(
        "-c",
        "--config",
        default=os.environ.get("MCP_GATEWAY_CONFIG", "config.yaml"),
    )

    migrate = sub.add_parser(
        "migrate",
        help="Apply pending database migrations and exit, without starting the server",
    )
    migrate.add_argument(
        "-c",
        "--config",
        default=os.environ.get("MCP_GATEWAY_CONFIG", "config.yaml"),
        help="Path to the YAML config file, used to find storage.path (default: "
        "config.yaml or $MCP_GATEWAY_CONFIG)",
    )

    rotate = sub.add_parser(
        "rotate-key",
        help="Rotate auth.encryption_key without re-encrypting the whole database",
    )
    rotate.add_argument(
        "-c",
        "--config",
        default=os.environ.get("MCP_GATEWAY_CONFIG", "config.yaml"),
        help="Path to the YAML config file, used to find storage.path (default: "
        "config.yaml or $MCP_GATEWAY_CONFIG)",
    )
    rotate.add_argument(
        "--old-key-file",
        required=True,
        help="File containing the key currently protecting the database",
    )
    rotate.add_argument(
        "--new-key-file",
        required=True,
        help="File containing the new key to protect the database with",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        # The mcp_gateway package already configured logging (and synced
        # $FASTMCP_LOG_LEVEL) from $MCP_GATEWAY_LOG_LEVEL at import time —
        # merely importing this module (via the `mcp-gateway` entry point)
        # triggers that, before argparse has even run. Force-reapply it here
        # so an explicit --log-level flag (which may differ from the env var)
        # actually takes effect; FASTMCP_LOG_LEVEL still needs a plain
        # assignment since fastmcp itself isn't imported until create_app()
        # runs below, so this value is what it will pick up.
        level = args.log_level.upper()
        os.environ["MCP_GATEWAY_LOG_LEVEL"] = level
        os.environ["FASTMCP_LOG_LEVEL"] = level
        logging.basicConfig(
            level=getattr(logging, level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            force=True,
        )

    if args.command == "hash-password":
        import bcrypt

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Passwords do not match", file=sys.stderr)
            return 1
        print(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
        return 0

    from mcp_gateway.config import load_config

    if args.command == "check":
        config = load_config(args.config)
        print(f"OK: {len(config.auth.users)} user(s), {len(config.backends)} backend(s)")
        return 0

    if args.command == "migrate":
        from mcp_gateway.config import load_storage_path
        from mcp_gateway.db_migrations import run_migrations_for_path

        storage_path = load_storage_path(args.config)
        run_migrations_for_path(storage_path)
        print(f"Database at {storage_path} is up to date")
        return 0

    if args.command == "rotate-key":
        from mcp_gateway.config import load_storage_path
        from mcp_gateway.storage import EncryptionKeyError, Storage

        storage_path = load_storage_path(args.config)
        old_key = Path(args.old_key_file).read_text().strip()
        new_key = Path(args.new_key_file).read_text().strip()
        try:
            Storage.rotate_key(storage_path, old_key, new_key)
        except EncryptionKeyError:
            print(
                f"Error: --old-key-file does not match the key currently protecting {storage_path}",
                file=sys.stderr,
            )
            return 1
        print(f"Encryption key rotated for {storage_path}")
        return 0

    if args.command == "run":
        import uvicorn

        from mcp_gateway.app import create_app
        from mcp_gateway.storage import EncryptionKeyError

        logger = logging.getLogger("mcp_gateway")
        logger.debug("Loading config from %s", args.config)
        config = load_config(args.config)
        logger.info(
            "Config loaded: %d user(s), %d backend(s) (%s)",
            len(config.auth.users),
            len(config.backends),
            ", ".join(sorted(config.backends)) or "none",
        )
        try:
            app = create_app(config)
        except EncryptionKeyError as exc:
            logger.error("%s (see the README's 'Encryption key' section)", exc)
            return 1
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=args.log_level,
            # The gateway sits behind a reverse proxy that terminates TLS.
            # Only the configured peer(s) are trusted to set X-Forwarded-*
            # (default: loopback only) -- see ServerConfig.trusted_proxy_ips.
            # ProxyHeadersMiddleware rewrites scope["client"] in place before
            # the request reaches the app *and* before uvicorn's own access
            # log line is emitted, so this is also what makes that log line
            # (and every request.client.host read elsewhere, e.g. rate
            # limiting) show the real client IP instead of the proxy's.
            proxy_headers=True,
            forwarded_allow_ips=config.server.trusted_proxy_ips,
            # Skip uvicorn's own logging.config.dictConfig() call, which
            # would otherwise attach separate, differently-formatted
            # handlers to the "uvicorn"/"uvicorn.error"/"uvicorn.access"
            # loggers with propagate=False. With no log_config, those
            # loggers keep logging's default propagate=True and pick up the
            # root handler/format installed by logging.basicConfig() above
            # -- the same one every mcp_gateway/httpx2/mcp.* log line uses --
            # so access logs are formatted consistently with the rest.
            log_config=None,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
