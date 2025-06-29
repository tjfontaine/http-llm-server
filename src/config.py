from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import json


class MCPConfig(BaseSettings):
    """
    Configuration for an individual MCP server.
    """

    model_config = SettingsConfigDict(env_prefix="mcp_")

    type: Literal["stdio", "sse", "streamable_http"]
    command: Optional[str] = None
    args: Optional[List[str]] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None


class Config(BaseSettings):
    """
    Typed configuration for HTTP LLM Server, loaded from environment variables or CLI args.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    port: int = 8080
    api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_base_url: Optional[str] = None
    openai_model_name: str = "gpt-4o"
    openai_temperature: float = 0.7
    max_turns: int = 25
    context_window_max: int = 0
    web_app_file: Optional[str] = None
    save_conversations: bool = False
    local_tools_enabled: bool = True
    one_shot: bool = False
    local_tools_stdio: bool = False
    log_level: str = "INFO"
    debug: bool = False
    openai_reasoning_max_tokens: Optional[int] = None
    mcp_servers: List[MCPConfig] = Field(default_factory=list)
    webapp_metadata: Dict[str, Any] = Field(default_factory=dict)
    system_prompt_template: Optional[str] = None
    error_llm_system_prompt_template: Optional[str] = None
    web_app_rules: Optional[str] = None

    @field_validator("mcp_servers", mode="before")
    def parse_mcp_servers(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON for MCP_SERVERS: {e}")
        return v

    @field_validator("log_level")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "TRACE"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()
