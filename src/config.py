from pydantic import BaseModel, Field, validator
from typing import List, Literal, Optional, Dict, Any
import json


class MCPConfig(BaseModel):
    """
    Configuration for an individual MCP server.
    """

    type: Literal["stdio", "sse", "streamable_http"]
    command: Optional[str] = None
    args: Optional[List[str]] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None


class Config(BaseModel):
    """
    Typed configuration for HTTP LLM Server, loaded from environment variables or CLI args.
    """

    port: int = Field(8080, env="PORT")
    api_key: str = Field(..., env="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(None, env="OPENAI_BASE_URL")
    openai_model_name: str = Field("gpt-4o", env="OPENAI_MODEL_NAME")
    openai_temperature: float = Field(0.7, env="OPENAI_TEMPERATURE")
    max_turns: int = Field(25, env="MAX_TURNS")
    context_window_max: int = Field(0, env="CONTEXT_WINDOW_MAX")
    web_app_file: Optional[str] = Field(None, env="WEB_APP_FILE")
    save_conversations: bool = Field(False, env="SAVE_CONVERSATIONS")
    local_tools_enabled: bool = Field(True, env="LOCAL_TOOLS_ENABLED")
    one_shot: bool = Field(False, env="ONE_SHOT")
    local_tools_stdio: bool = Field(False, env="LOCAL_TOOLS_STDIO")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    debug: bool = Field(False, env="DEBUG")
    mcp_servers: List[MCPConfig] = Field(default_factory=list, env="MCP_SERVERS")
    webapp_metadata: Dict[str, Any] = Field(default_factory=dict)
    system_prompt_template: Optional[str] = None
    error_llm_system_prompt_template: Optional[str] = None
    web_app_rules: Optional[str] = None

    @validator("mcp_servers", pre=True)
    def parse_mcp_servers(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON for MCP_SERVERS: {e}")
        return v

    @validator("log_level")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "TRACE"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()
