import json
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServerConfig(BaseSettings):
    """Pydantic model for a single MCP server's configuration."""

    type: str
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    module: Optional[str] = None  # For simplified stdio config


class Config(BaseSettings):
    """
    Typed configuration for HTTP LLM Server, loaded from environment variables or
    CLI args.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        cli_parse_args=True,
        cli_prog_name="http-llm-server",
    )

    port: int = Field(default=8080, alias="PORT")
    host: str = Field(default="0.0.0.0", alias="HOST")
    api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")
    openai_model_name: str = Field(default="gpt-4o", alias="OPENAI_MODEL_NAME")
    openai_temperature: float = Field(default=0.7, alias="OPENAI_TEMPERATURE")
    openai_reasoning_max_tokens: Optional[int] = Field(
        default=None, alias="OPENAI_REASONING_MAX_TOKENS"
    )
    max_turns: int = Field(default=25, alias="MAX_TURNS")
    context_window_max: int = Field(default=0, alias="CONTEXT_WINDOW_MAX")
    web_app_file: Optional[str] = Field(default=None, alias="WEB_APP_FILE")
    save_conversations: bool = Field(default=False, alias="SAVE_CONVERSATIONS")
    local_tools_enabled: bool = Field(default=True, alias="LOCAL_TOOLS_ENABLED")
    one_shot: Optional[int] = Field(
        default=None,
        alias="ONE_SHOT",
        description="Run in one-shot mode for N conversations, then exit.",
    )
    local_tools_stdio: bool = Field(default=False, alias="LOCAL_TOOLS_STDIO")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")

    # The following fields are not loaded from env but are set programmatically
    mcp_servers: List[Dict[str, Any]] = Field(default_factory=list)
    webapp_metadata: Dict[str, Any] = Field(default_factory=dict)
    system_prompt_template: str = ""
    error_llm_system_prompt_template: str = ""
    web_app_rules: str = ""
    openai_max_tokens: Optional[int] = None

    @field_validator("mcp_servers", mode="before")
    def parse_mcp_servers(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("mcp_servers must be a valid JSON string or a list")
        return v

    @field_validator("log_level")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "TRACE"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()

    def __init__(self, **kwargs):
        """Initialize Config and load web app content if web_app_file is provided."""
        # Check if web_app_file was explicitly passed
        explicit_web_app_file = kwargs.get("web_app_file")

        # Remove web_app_file from kwargs to prevent BaseSettings from processing it
        if "web_app_file" in kwargs:
            del kwargs["web_app_file"]

        super().__init__(**kwargs)

        # Always load the base system prompt first.
        self._load_system_prompt()

        # Set web_app_file to the explicit value if provided, otherwise use default
        if explicit_web_app_file is not None:
            self.web_app_file = explicit_web_app_file
        elif not self.web_app_file:
            self.web_app_file = self._get_default_info_site_path()

        # Load web app content if web_app_file is provided (which now includes
        # the default)
        if self.web_app_file:
            self._load_web_app_content()

    def _get_default_info_site_path(self) -> str:
        """Returns path to the default info site prompt."""
        import os

        # __file__ is src/config.py, so go up one level to find examples
        default_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "examples",
            "default_info_site",
            "prompt.md",
        )
        return default_path

    def _load_system_prompt(self):
        """Loads the system prompt from a file or uses a fallback."""
        import os

        # Try to load from src/prompts/system.md relative to project root
        # __file__ is src/config.py, so go up one level to get to
        # src/prompts/system.md
        system_prompt_path = os.path.join(
            os.path.dirname(__file__), "prompts", "system.md"
        )
        try:
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt_template = f.read()
        except Exception:
            # If we can't load the file, use a basic fallback prompt
            self.system_prompt_template = (
                "You are an advanced AI assistant powering a web server. Your primary "
                "goal is to act as a fully-featured web server, responding to raw "
                "HTTP requests with raw HTTP responses. You must generate the "
                "entire HTTP response, including the status line, headers, and body."
            )

    def _load_web_app_content(self) -> None:
        """Load content from the web app file into configuration fields."""
        import os
        import re

        import yaml

        if not os.path.exists(self.web_app_file):
            return

        try:
            with open(self.web_app_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract YAML front matter and content
            yaml_match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
            if yaml_match:
                try:
                    # Parse YAML metadata
                    metadata = yaml.safe_load(yaml_match.group(1))
                    if metadata:
                        self.webapp_metadata = metadata

                        # Extract MCP servers for later use
                        if "mcp_servers" in metadata:
                            # Note: MCP servers are handled separately in core_services
                            pass

                    # Extract content portion as system prompt template
                    content_portion = yaml_match.group(2).strip()
                    if content_portion:
                        self.web_app_rules = content_portion

                except yaml.YAMLError:
                    # If YAML parsing fails, use the entire content
                    self.web_app_rules = content
            else:
                # No YAML front matter, use entire content
                self.web_app_rules = content

        except Exception:
            # On any error, silently continue without web app content
            pass

    @classmethod
    def parse_web_app_file(cls, web_app_file: str) -> List[Dict[str, Any]]:
        """Parse web app file and extract MCP server configurations."""
        import os
        import re

        import yaml

        if not os.path.exists(web_app_file):
            return []

        try:
            with open(web_app_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract YAML front matter
            yaml_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if yaml_match:
                try:
                    metadata = yaml.safe_load(yaml_match.group(1))
                    if metadata and "mcp_servers" in metadata:
                        servers = metadata["mcp_servers"]
                        if isinstance(servers, list):
                            return servers
                except yaml.YAMLError:
                    pass
        except Exception:
            pass

        return []
