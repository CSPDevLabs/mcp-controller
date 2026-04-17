"""Unit tests for the mock_intercept decorator (src/mcp_controller/core/mock.py)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from mcp_controller.config import Settings
from mcp_controller.core.mock import (
    MockDataNotFoundError,
    _data_dir,
    _extract_args,
    _mock_lookup,
    _mock_save,
    _serialize_result,
    mock_intercept,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _settings(**kwargs) -> Settings:
    """Create a Settings instance with test defaults and any overrides."""
    return Settings(
        mock=kwargs.get("mock", False),
        mock_data_record=kwargs.get("mock_data_record", False),
    )


class SampleModel(BaseModel):
    name: str
    value: int


# ---------------------------------------------------------------------------
# _data_dir
# ---------------------------------------------------------------------------


def test_data_dir_points_to_tests_mocks_data():
    path = _data_dir()
    assert path.name == "data"
    assert path.parent.name == "mocks"
    assert path.parent.parent.name == "tests"
    assert (path.parent.parent.parent / "src").exists(), "Project root not found"


# ---------------------------------------------------------------------------
# _extract_args
# ---------------------------------------------------------------------------


async def _sample_func(device: str, interval: str, ctx, step: str = "15s"):
    pass


def test_extract_args_excludes_ctx():
    ctx_mock = object()
    result = _extract_args(
        _sample_func,
        args=("bng1", "1h", ctx_mock),
        kwargs={},
        exclude=frozenset({"ctx"}),
    )
    assert result == {"device": "bng1", "interval": "1h", "step": "15s"}


def test_extract_args_applies_defaults():
    ctx_mock = object()
    result = _extract_args(
        _sample_func,
        args=(),
        kwargs={"device": "bng2", "interval": "5m", "ctx": ctx_mock},
        exclude=frozenset({"ctx"}),
    )
    assert result["step"] == "15s"


def test_extract_args_serializes_pydantic():
    class _Func:
        pass

    import inspect

    async def func_with_model(model: SampleModel):
        pass

    m = SampleModel(name="test", value=42)
    result = _extract_args(func_with_model, args=(m,), kwargs={}, exclude=frozenset())
    assert result == {"model": {"name": "test", "value": 42}}


# ---------------------------------------------------------------------------
# _serialize_result
# ---------------------------------------------------------------------------


def test_serialize_result_pydantic():
    m = SampleModel(name="x", value=1)
    assert _serialize_result(m) == {"name": "x", "value": 1}


def test_serialize_result_list_of_pydantic():
    items = [SampleModel(name="a", value=1), SampleModel(name="b", value=2)]
    result = _serialize_result(items)
    assert result == [{"name": "a", "value": 1}, {"name": "b", "value": 2}]


def test_serialize_result_string():
    assert _serialize_result("hello") == "hello"


def test_serialize_result_dict():
    d = {"key": "val"}
    assert _serialize_result(d) == d


# ---------------------------------------------------------------------------
# _mock_save and _mock_lookup
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect _data_dir() to a temporary directory."""
    monkeypatch.setattr("mcp_controller.core.mock._data_dir", lambda override=None: tmp_path)
    return tmp_path


def test_mock_save_creates_file(tmp_data_dir):
    _mock_save("my_func", {"a": 1}, "result_string")
    file = tmp_data_dir / "my_func.json"
    assert file.exists()
    data = json.loads(file.read_text())
    assert len(data) == 1
    assert data[0]["args"] == {"a": 1}
    assert data[0]["mock_data"] == "result_string"
    assert data[0]["response_type"] == "str"


def test_mock_save_upserts_same_args(tmp_data_dir):
    _mock_save("fn", {"x": 1}, "first")
    _mock_save("fn", {"x": 1}, "second")
    data = json.loads((tmp_data_dir / "fn.json").read_text())
    # Only one entry — the second upserted the first.
    assert len(data) == 1
    assert data[0]["mock_data"] == "second"


def test_mock_save_appends_different_args(tmp_data_dir):
    _mock_save("fn", {"x": 1}, "first")
    _mock_save("fn", {"x": 2}, "second")
    data = json.loads((tmp_data_dir / "fn.json").read_text())
    assert len(data) == 2


def test_mock_save_pydantic_result(tmp_data_dir):
    m = SampleModel(name="n", value=7)
    _mock_save("fn", {"k": "v"}, m)
    data = json.loads((tmp_data_dir / "fn.json").read_text())
    assert data[0]["mock_data"] == {"name": "n", "value": 7}
    assert data[0]["response_type"] == "SampleModel"


def test_mock_lookup_returns_matching_entry(tmp_data_dir):
    _mock_save("fn", {"a": 1}, "expected")
    result = _mock_lookup("fn", {"a": 1})
    assert result == "expected"


def test_mock_lookup_raises_when_no_file(tmp_data_dir):
    with pytest.raises(MockDataNotFoundError) as exc_info:
        _mock_lookup("nonexistent", {"a": 1})
    assert "nonexistent" in str(exc_info.value)


def test_mock_lookup_raises_when_args_not_found(tmp_data_dir):
    _mock_save("fn", {"a": 1}, "data")
    with pytest.raises(MockDataNotFoundError) as exc_info:
        _mock_lookup("fn", {"a": 99})
    err = exc_info.value
    assert err.func_name == "fn"
    assert err.call_args == {"a": 99}
    assert len(err.available) == 1


# ---------------------------------------------------------------------------
# mock_intercept decorator — normal mode (no-op)
# ---------------------------------------------------------------------------


def test_normal_mode_returns_unwrapped_function():
    settings = _settings()
    call_count = 0

    async def my_handler(x: int):
        nonlocal call_count
        call_count += 1
        return x * 2

    wrapped = mock_intercept(settings)(my_handler)
    # In normal mode the decorator must return the original function.
    assert wrapped is my_handler


# ---------------------------------------------------------------------------
# mock_intercept decorator — record mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_mode_calls_function_and_saves(tmp_data_dir):
    settings = _settings(mock_data_record=True)

    async def my_handler(x: int):
        return f"result_{x}"

    wrapped = mock_intercept(settings)(my_handler)
    result = await wrapped(x=42)

    assert result == "result_42"
    file = tmp_data_dir / "my_handler.json"
    assert file.exists()
    data = json.loads(file.read_text())
    assert data[0]["args"] == {"x": 42}
    assert data[0]["mock_data"] == "result_42"


@pytest.mark.asyncio
async def test_record_mode_preserves_function_name(tmp_data_dir):
    settings = _settings(mock_data_record=True)

    async def my_named_func():
        return "ok"

    wrapped = mock_intercept(settings)(my_named_func)
    assert wrapped.__name__ == "my_named_func"


# ---------------------------------------------------------------------------
# mock_intercept decorator — replay mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_mode_returns_cached_data_without_calling_handler(tmp_data_dir):
    # Pre-populate mock data.
    (tmp_data_dir / "my_tool.json").write_text(
        json.dumps([{"args": {"device": "bng1"}, "response_type": "str", "mock_data": "cached"}])
    )

    settings = _settings(mock=True)
    real_called = False

    async def my_tool(device: str):
        nonlocal real_called
        real_called = True
        return "real_result"

    wrapped = mock_intercept(settings)(my_tool)
    result = await wrapped(device="bng1")

    assert result == "cached"
    assert not real_called, "Real handler must not be called in replay mode"


@pytest.mark.asyncio
async def test_replay_mode_raises_when_no_match(tmp_data_dir):
    settings = _settings(mock=True)

    async def my_tool(device: str):
        return "real"

    wrapped = mock_intercept(settings)(my_tool)

    with pytest.raises(MockDataNotFoundError):
        await wrapped(device="unknown_device")


@pytest.mark.asyncio
async def test_replay_mode_excludes_ctx_from_args(tmp_data_dir):
    """Args key must not include `ctx` even when passed as positional."""
    (tmp_data_dir / "my_tool.json").write_text(
        json.dumps(
            [{"args": {"device": "bng1"}, "response_type": "str", "mock_data": "cached"}]
        )
    )

    settings = _settings(mock=True)
    ctx_mock = AsyncMock()

    async def my_tool(device: str, ctx):
        return "real"

    wrapped = mock_intercept(settings)(my_tool)
    result = await wrapped("bng1", ctx_mock)
    assert result == "cached"
