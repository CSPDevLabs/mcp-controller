"""Configuration management using pydantic-settings BaseSettings."""

from typing import Literal, Tuple, Type

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings loaded from env vars and optional YAML config.

    Priority order (highest to lowest):
        1. Constructor keyword arguments (CLI overrides)
        2. YAML file — set `yaml_file` in `model_config` or subclass with a custom path
        3. Environment variables (prefix ``MCP_``)
        4. ``.env`` file
        5. Field defaults

    YAML loading is opt-in: no file is read unless `yaml_file` is set in
    `model_config` (or a subclass overrides it).  Missing files are silently
    ignored by `YamlConfigSettingsSource`.
    """

    # Additional flags
    # config.py
    app_name: str = Field(
        default="mcp-controller", description="App name for structured log fields"
    )
    debug: bool = False
    mock: bool = False
    mock_data_record: bool = False
    mock_data_dir: str | None = Field(
        default=None,
        description="Path to mock data directory. When unset, resolved "
        "relative to the source tree via __file__. Set explicitly "
        "in Docker / installed environments where the source tree "
        "is absent (e.g. MCP_MOCK_DATA_DIR=/app/tests/mocks/data).",
    )
    environment: str = "dev"

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        yaml_file=None,  # no YAML file by default; set to a path to enable
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Return settings sources in priority order, inserting YAML between init and env.

        Args:
            settings_cls: The `Settings` class being constructed.
            init_settings: Values passed directly to the constructor.
            env_settings: Values from environment variables.
            dotenv_settings: Values from the `.env` file.
            file_secret_settings: Values from secrets files.

        Returns:
            Tuple of sources ordered highest-to-lowest priority.
        """
        # If `yaml_file` was passed as a constructor kwarg (e.g. from --config CLI arg),
        # forward it directly to YamlConfigSettingsSource; otherwise fall back to model_config.
        yaml_file = init_settings.init_kwargs.get("yaml_file")  # type: ignore[attr-defined]
        yaml_source = (
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)
            if yaml_file is not None
            else YamlConfigSettingsSource(settings_cls)
        )
        return (
            init_settings,
            yaml_source,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    tls_skip_verify: bool = Field(
        default=False,
        description="Disable TLS certificate verification for outbound HTTP clients. "
        "Maps to httpx.AsyncClient `verify` parameter (True=verify, False=skip).",
    )
    prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Prometheus HTTP API base URL",
    )
    loki_url: str = Field(
        default="http://localhost:3100",
        description="Loki HTTP API base URL",
    )
    k8s_namespace: str = Field(
        default="nok-bng",
        description="Default Kubernetes namespace for CRD queries (nok.dev targets)",
    )
    host: str = Field(
        default="0.0.0.0",
        description="HTTP server bind host",
    )
    port: int = Field(
        default=8000,
        description="HTTP server bind port",
    )
    log_level: LogLevel = Field(
        default="INFO",
        description="Logging level",
    )
