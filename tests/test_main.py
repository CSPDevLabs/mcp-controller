"""Tests for mcp_controller main module."""

import sys
from unittest.mock import patch

import pytest


def test_main_help_flag():
    """Main should support --help flag and exit cleanly."""
    from mcp_controller.__main__ import main

    with patch.object(sys, "argv", ["mcp-controller", "--help"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
