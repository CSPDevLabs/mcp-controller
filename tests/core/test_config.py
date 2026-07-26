"""Tests for configuration management."""

import os
import tempfile

import yaml

from mcp_controller.config import Settings


class TestSettings:
    """Tests for Settings BaseSettings class."""

    def test_defaults(self):
        """Settings should have sensible defaults."""
        settings = Settings()
        assert settings.prometheus_url == "http://localhost:9090"
        assert settings.loki_url == "http://localhost:3100"
        assert settings.host == "0.0.0.0"
        assert settings.port == 8088
        assert settings.log_level == "INFO"

    def test_env_vars(self, monkeypatch):
        """Settings should load from MCP_ prefixed env vars."""
        monkeypatch.setenv("MCP_PROMETHEUS_URL", "http://prom:9090")
        monkeypatch.setenv("MCP_LOKI_URL", "http://loki:3100")
        monkeypatch.setenv("MCP_HOST", "127.0.0.1")
        monkeypatch.setenv("MCP_PORT", "9000")
        monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")

        settings = Settings()
        assert settings.prometheus_url == "http://prom:9090"
        assert settings.loki_url == "http://loki:3100"
        assert settings.host == "127.0.0.1"
        assert settings.port == 9000
        assert settings.log_level == "DEBUG"

    def test_from_yaml(self, tmp_path):
        """Settings should load from YAML file."""
        config = {
            "prometheus_url": "http://yaml-prom:9090",
            "loki_url": "http://yaml-loki:3100",
            "port": 7000,
        }
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump(config))

        settings = Settings(yaml_file=yaml_file)
        assert settings.prometheus_url == "http://yaml-prom:9090"
        assert settings.loki_url == "http://yaml-loki:3100"
        assert settings.port == 7000
        # Defaults still apply for unset fields
        assert settings.host == "0.0.0.0"

    def test_yaml_overrides_env(self, monkeypatch, tmp_path):
        """YAML config should take precedence over env vars."""
        monkeypatch.setenv("MCP_PROMETHEUS_URL", "http://env-prom:9090")

        config = {"prometheus_url": "http://yaml-prom:9090"}
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump(config))

        settings = Settings(yaml_file=yaml_file)
        assert settings.prometheus_url == "http://yaml-prom:9090"

    def test_cli_overrides(self, tmp_path):
        """Direct kwargs should override YAML."""
        config = {"port": 7000}
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump(config))

        settings = Settings(yaml_file=yaml_file, port=9999)
        assert settings.port == 9999

    def test_missing_yaml_uses_defaults(self):
        """Non-existent YAML file should fall back to defaults gracefully."""
        settings = Settings(yaml_file="/nonexistent/config.yaml")
        assert settings.prometheus_url == "http://localhost:9090"
