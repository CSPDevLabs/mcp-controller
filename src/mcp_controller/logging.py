import logging
import socket
import sys

from pythonjsonlogger.json import JsonFormatter
from mcp_controller.config import Settings


# Syslog RFC 5424 formatter
class SyslogRFC5424Formatter(JsonFormatter):
    """JSON formatter that includes standard fields for structured logging."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA MSG
            fmt="%(asctime)s %(levelname)s %(name)s %(process)d %(funcName)s %(request_id)s \
                %(message)s",
            rename_fields={
                "asctime": "timestamp",  # RFC 5424: TIMESTAMP
                "levelname": "severity",  # RFC 5424: derived from PRI
                "name": "logger",  # RFC 5424: could map to APP-NAME or SD
                "process": "procid",  # RFC 5424: PROCID
                "funcName": "msgid",  # RFC 5424: MSGID (message type identifier)
                "message": "msg",  # RFC 5424: MSG
            },
            datefmt="%Y-%m-%dT%H:%M:%S%z",  # RFC 3339 format
            # (removed %f as it's not supported by time.strftime)
            style="%",
            defaults={
                "version": 1,  # RFC 5424: VERSION (always 1)
                "facility": 1,  # RFC 5424: user-level (1)
                "hostname": socket.gethostname(),  # RFC 5424: HOSTNAME
                "app_name": settings.app_name,  # RFC 5424: APP-NAME
                "environment": settings.environment,
            },
        )


def setup_logging(settings: Settings) -> None:
    """Setup the logging for the application.

    This function is idempotent, because it clears existing handlers
    before adding new ones.

    Args:
        settings: Application settings providing `log_level` and `app_name`.

    Returns:
        None
    """
    # Get the logging level
    level = getattr(logging, settings.log_level)

    # Build the handler + formatter pair
    handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(SyslogRFC5424Formatter(settings))

    # Apply to "mcp-controller" logger (parent of all "mcp-controller.*" loggers)
    root: logging.Logger = logging.getLogger("mcp-controller")
    # Remove any existing handlers
    root.handlers.clear()
    # Adding only one handler
    root.addHandler(handler)
    # Setting the level
    root.setLevel(level)
