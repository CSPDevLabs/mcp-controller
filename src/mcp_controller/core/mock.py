"""Record/replay mock interceptor for MCP handlers.

Provides the `mock_intercept` decorator that adds transparent record and replay
support to MCP resource, tool, and completion handlers without modifying handler
business logic.

Modes
-----
- **Normal** (default): decorator is a no-op — the original function is returned
  unwrapped with zero runtime overhead.
- **Record** (`settings.mock_data_record = True`): calls the real handler, then
  serialises args + result into `tests/mocks/data/<function_name>.json`. Existing
  entries for the same args are replaced (upsert).
- **Replay** (`settings.mock = True`): short-circuits before the handler body
  executes, looks up the pre-recorded response by matching args, and returns it
  directly. Raises `MockDataNotFoundError` if no matching entry is found.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from pathlib import Path
from typing import Any
from mcp.server.fastmcp.exceptions import ResourceError
from mcp_controller.core.types import ErrorResponse

from pydantic import BaseModel

from mcp_controller.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MockDataNotFoundError(Exception):
    """Raised in replay mode when no recorded entry matches the call args.

    Args:
        func_name: Name of the handler function.
        call_args: Serialized call arguments that were looked up.
        available: List of recorded arg dicts available in the file.
    """

    def __init__(  # noqa: B042
        self, func_name: str, call_args: dict, available: list[dict]
    ) -> None:
        self.func_name = func_name
        self.call_args = call_args
        self.available = available
        msg = (
            f"No mock data found for '{func_name}' with args {call_args}. "
            f"Available entries: {available}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _data_dir(override: str | None = None) -> Path:
    """Return the path to the mock data directory.

    When `override` is set (from `Settings.mock_data_dir`), it is used as-is.
    Otherwise the path is resolved relative to the source tree via `__file__`
    — this works in local dev but **not** when the package is installed as a
    wheel (e.g. inside Docker), because `__file__` then points into
    site-packages.

    Args:
        override: Explicit directory path, typically from
            `Settings.mock_data_dir` / `MCP_MOCK_DATA_DIR`.

    Returns:
        Absolute `Path` to the mock data directory.
    """
    if override:
        return Path(override).resolve()
    # __file__ = src/mcp_controller/core/mock.py
    # parents[0] = src/mcp_controller/core/
    # parents[1] = src/mcp_controller/
    # parents[2] = src/
    # parents[3] = <project_root>/
    return Path(__file__).resolve().parents[3] / "tests" / "mocks" / "data"


def _extract_args(
    func: Any,
    args: tuple,
    kwargs: dict,
    exclude: frozenset[str],
) -> dict:
    """Bind positional and keyword arguments to parameter names.

    Applies default values for omitted parameters, then excludes non-serializable
    params (e.g. `ctx`, `context`). Pydantic `BaseModel` instances are serialized
    via `model_dump(mode="json")`.

    Args:
        func: The original handler function.
        args: Positional arguments from the wrapper call.
        kwargs: Keyword arguments from the wrapper call.
        exclude: Set of parameter names to omit (e.g. `{"ctx", "context"}`).

    Returns:
        Dict mapping parameter name to its JSON-serializable value.
    """
    sig = inspect.signature(func)
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        # Fallback: bind only kwargs, ignore positional mismatches
        bound = sig.bind_partial(**kwargs)
    bound.apply_defaults()

    result: dict = {}
    for name, value in bound.arguments.items():
        if name in exclude:
            continue
        if isinstance(value, BaseModel):
            result[name] = value.model_dump(mode="json")
        else:
            result[name] = value
    return result


def _serialize_result(result: Any) -> Any:
    """Serialize a handler return value to a JSON-compatible structure.

    Args:
        result: The handler's return value.

    Returns:
        A JSON-serializable object: dict, list of dicts, string, or primitive.
    """
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in result
        ]
    if isinstance(result, Exception):
        return {"error": type(result).__name__, "detail": str(result)}
    return result


def _mock_save(
    func_name: str, call_args: dict, result: Any, data_dir_override: str | None = None
) -> None:
    """Persist a recorded handler response to disk (upsert by args).

    Loads the existing JSON file (or starts with an empty list), replaces any
    entry whose `args` match `call_args`, then appends the new entry and writes
    the file back.

    Args:
        func_name: Name of the handler function (used as the filename stem).
        call_args: Serialized call arguments — acts as the lookup key.
        result: The handler's return value to persist.
        data_dir_override: Explicit data directory path from Settings.
    """
    data_dir = _data_dir(data_dir_override)
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / f"{func_name}.json"

    entries: list[dict] = []
    if file_path.exists():
        try:
            entries = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read existing mock file %s: %s", file_path, exc)
            entries = []

    response_type = type(result).__name__
    mock_data = _serialize_result(result)

    # Upsert: replace existing entry with same args, or append new one.
    new_entry = {"args": call_args, "response_type": response_type, "mock_data": mock_data}
    updated = [e for e in entries if e.get("args") != call_args]
    updated.append(new_entry)
    try:
        file_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.error(
            "Failed to write mock data for %s args=%s to %s: %s",
            func_name,
            call_args,
            file_path,
            exc,
        )
        raise

    logger.debug(
        "[mock_intercept] Recorded %s args=%s -> %s (%d total entries)",
        func_name,
        call_args,
        response_type,
        len(updated),
    )


def _mock_lookup(func_name: str, call_args: dict, data_dir_override: str | None = None) -> Any:
    """Look up a pre-recorded response by function name and args.

    Args:
        func_name: Name of the handler function.
        call_args: Serialized call arguments to match against.
        data_dir_override: Explicit data directory path from Settings.

    Returns:
        The `mock_data` value from the matching entry.

    Raises:
        MockDataNotFoundError: If the JSON file does not exist or no entry
            matches `call_args`.
    """
    file_path = _data_dir(data_dir_override) / f"{func_name}.json"
    if not file_path.exists():
        logging.warning(
            "[mock_intercept] No mock data file found for %s at %s", func_name, file_path
        )
        raise MockDataNotFoundError(func_name, call_args, [])

    try:
        entries: list[dict] = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise MockDataNotFoundError(func_name, call_args, []) from exc

    for entry in entries:
        if entry.get("args") == call_args:
            response_type = entry.get("response_type", "unknown")
            mock_data = entry.get("mock_data")
            logger.debug(
                "[mock_intercept] Replaying %s args=%s -> %s",
                func_name,
                call_args,
                response_type,
            )
            # Replay recorded exceptions: re-raise the same type so
            # handlers see identical behavior to record mode.
            if isinstance(mock_data, dict):
                if response_type == "ValueError":
                    raise ValueError(mock_data.get("detail", ""))
                if response_type == "ResourceError":
                    raise ResourceError(mock_data.get("detail", ""))
                if response_type == "ErrorResponse":
                    raise ErrorResponse(**mock_data)
                if response_type == "NotImplementedError":
                    raise NotImplementedError(mock_data.get("detail", ""))
            return mock_data

    available = [e.get("args", {}) for e in entries]
    raise MockDataNotFoundError(func_name, call_args, available)


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

_DEFAULT_EXCLUDE: frozenset[str] = frozenset({"ctx", "context"})


def mock_intercept(
    settings: Settings,
    exclude_args: frozenset[str] = _DEFAULT_EXCLUDE,
):
    """Decorator factory adding record/replay mock support to MCP handlers.

    In **normal mode** the decorator is a no-op: the unwrapped function is
    returned directly so there is zero runtime overhead in production.

    In **record mode** (`settings.mock_data_record = True`) the real handler is
    called and its result is persisted to `tests/mocks/data/<func_name>.json`.

    In **replay mode** (`settings.mock = True`) the handler body is never
    executed. Instead the decorator returns the pre-recorded response that
    matches the current call arguments.

    Args:
        settings: Application settings. Reads `mock` and `mock_data_record`.
        exclude_args: Parameter names to omit from the args key (default:
            `{"ctx", "context"}`).

    Returns:
        A decorator that wraps the target async function.

    Example::

        @mcp.tool()
        @mock_intercept(settings)
        async def bng_device_cpu_usage(device: str, interval: str, ctx: Context):
            ...
    """

    def decorator(func: Any) -> Any:
        # Normal mode — return the function unwrapped (zero overhead).
        if not settings.mock and not settings.mock_data_record:
            return func

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_args = _extract_args(func, args, kwargs, exclude_args)

            if settings.mock:
                # Replay — return cached data without calling the handler.
                return _mock_lookup(func.__name__, call_args, settings.mock_data_dir)

            # Record — call the real handler, then persist args + result.
            try:
                result = await func(*args, **kwargs)
                _mock_save(func.__name__, call_args, result, settings.mock_data_dir)
            except Exception as exc:
                logger.warning(
                    "[mock_intercept] Handler %s raised an exception: %s",
                    func.__name__,
                    exc,
                )

                _mock_save(func.__name__, call_args, exc, settings.mock_data_dir)
                raise
            return result

        return wrapper

    return decorator
