"""Main entry point for mcp-controller."""

import argparse
import sys
from mcp_controller.server import create_server
from mcp_controller.config import Settings
from mcp_controller.logging import setup_logging


def main() -> int:
    """Run the MCP Controller application."""
    parser = argparse.ArgumentParser(description="Nokia BNG MCP Controller")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="HTTP server bind host (overrides config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP server bind port (overrides config)",
    )
    mock_group = parser.add_mutually_exclusive_group()
    mock_group.add_argument(
        "--mock",
        action="store_true",
        default=None,
        help="Run in mock (replay) mode: serve pre-recorded responses from tests/mocks/data/",
    )
    mock_group.add_argument(
        "--mock-data-record",
        action="store_true",
        default=None,
        help="Run in record mode: capture handler responses to tests/mocks/data/",
    )
    args = parser.parse_args()

    # Applying overrides
    overrides: dict = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.config is not None:
        overrides["yaml_file"] = args.config
    if args.mock:
        overrides["mock"] = True
    if args.mock_data_record:
        overrides["mock_data_record"] = True

    settings = Settings(**overrides)

    # Setting up logging
    setup_logging(settings)

    # logging.basicConfig(
    #     level=getattr(logging, settings.log_level.upper(), logging.INFO),
    #     format="%(asctime)s %(name)s %(levelname)s %(message)s",
    # )

    server = create_server(settings)
    server.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    sys.exit(main())
