from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import dspy
from dspy.teleprompt import BootstrapFewShot
from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    enable_verbose_stdout_logging,
    set_tracing_disabled,
)
from agents.mcp import (
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.model_settings import ModelSettings
from aiohttp import web

from src.app import handle_http_request
from src.config import Config, McpServerConfig
from src.logging_config import get_loggers
from src.dspy_module import HttpProgram
from src.training_data import training_data
from src.server.middleware import (
    error_handling_middleware,
    logging_and_metrics_middleware,
    session_cleanup_middleware,
    session_middleware,
)

if TYPE_CHECKING:
    pass


app_logger, _, _ = get_loggers()


class WebServer:
    """A wrapper for the aiohttp web server."""

    def __init__(
        self,
        port: int,
        host: str,
        mcp_servers_config: list = [],
        log_level: str = "INFO",
        web_app_file: str = None,
        config: Config = None,
    ):
        self.port = port
        self.host = host
        self.mcp_servers_config = mcp_servers_config
        self.log_level = log_level
        self.web_app_file = web_app_file
        self.config = config
        # Note: Logging is configured by core_services.py main() for the subprocess

        # Enable verbose agents library logging if TRACE level is requested
        if log_level == "TRACE":
            app_logger.debug("Enabling verbose agents library logging for TRACE level")
            enable_verbose_stdout_logging()
        self.app = web.Application(
            middlewares=[
                logging_and_metrics_middleware(),
                session_cleanup_middleware(),
                error_handling_middleware(),
                session_middleware(),
            ]
        )
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.agent: Agent | None = None
        self.mcp_server_lifecycles: list = []

    async def initialize_agent(self):
        """
        Initialize MCP servers based on configuration and create an agent with them.
        """
        config = self.app.get('config') or self.config
        if not config:
            raise ValueError("Configuration not found in WebServer or app context.")

        mcp_servers = []

        # Initialize external MCP servers from typed config
        mcp_servers_config = getattr(config, 'mcp_servers_config', None) or config.mcp_servers or []
        if mcp_servers_config:
            app_logger.debug(
                "Processing %d MCP server configurations...",
                len(mcp_servers_config),
            )
            for i, mcp_cfg_dict in enumerate(mcp_servers_config):
                app_logger.debug(
                    "Processing MCP server %d/%d: %s",
                    i + 1,
                    len(mcp_servers_config),
                    mcp_cfg_dict,
                )
                mcp_config = McpServerConfig(**mcp_cfg_dict)
                server = None

                if mcp_config.type.lower() == "stdio":
                    # Handle simplified stdio config that just specifies a module
                    if "module" in mcp_cfg_dict:
                        app_logger.debug(
                            "Using module-based stdio config: %s",
                            mcp_cfg_dict["module"],
                        )
                        params = {
                            "global_state": self.app.get("global_state"),
                            "command": sys.executable,
                            "args": ["-m", mcp_cfg_dict["module"]],
                        }
                    else:
                        app_logger.debug("Using full stdio config")
                        # Handle full stdio config
                        params = {
                            "global_state": self.app.get("global_state"),
                            "command": mcp_config.command,
                            "args": mcp_config.args or [],
                            "cwd": mcp_config.cwd,
                            "env": mcp_config.env,
                        }

                    server = MCPServerStdio(params=params)
                elif mcp_config.type.lower() == "sse":
                    app_logger.debug(f"Creating SSE server with URL: {mcp_config.url}")
                    server = MCPServerSse(params={"url": mcp_config.url})
                elif mcp_config.type.lower() == "streamable_http":
                    app_logger.debug(
                        f"Creating StreamableHttp server with URL: {mcp_config.url}"
                    )
                    server = MCPServerStreamableHttp(params={"url": mcp_config.url})
                else:
                    app_logger.error(f"Unsupported MCP server type: {mcp_config.type}")
                    continue

                # Initialize server
                await server.__aenter__()
                if server:
                    tools = await server.list_tools()
                    app_logger.debug(
                        "Server %s provides %d tools",
                        server.name,
                        len(tools) if tools else 0,
                    )
                    mcp_servers.append(server)
                    if tools:
                        tool_names = sorted(t.name for t in tools)
                        app_logger.info(
                            "Connected to MCP server: %s (%d tools)",
                            server.name,
                            len(tools),
                        )
                        app_logger.debug("Available tools: %s", tool_names)
                    self.mcp_server_lifecycles.append(server)

        # Create the main agent with all configured MCP servers
        app_logger.debug("Creating OpenAI model")
        if config.openai_base_url:
            app_logger.debug(
                f"Using custom OpenAI client with base_url: {config.openai_base_url}"
            )
            custom_client = AsyncOpenAI(
                api_key=config.api_key, base_url=config.openai_base_url
            )
            model = OpenAIChatCompletionsModel(
                model=config.openai_model_name, openai_client=custom_client
            )
            set_tracing_disabled(disabled=True)
        else:
            app_logger.debug(f"Using default model: {config.openai_model_name}")
            model = config.openai_model_name

        # Create Agent
        app_logger.debug(f"Creating agent with {len(mcp_servers)} MCP server(s)")
        self.agent = Agent(
            name="HTTP LLM Server Agent",
            instructions=None,  # Set to None so system prompt takes precedence
            model=model,
            model_settings=ModelSettings(
                temperature=config.openai_temperature,
                include_usage=True,
                reasoning=(
                    {"max_tokens": config.openai_reasoning_max_tokens}
                    if config.openai_reasoning_max_tokens is not None
                    else None
                ),
            ),
            mcp_servers=mcp_servers,
        )

        app_logger.info(f"Agent initialized with {len(mcp_servers)} MCP server(s)")
        self.app["agent"] = self.agent

        # Configure and compile DSPy program
        app_logger.info("Compiling DSPy program...")
        try:
            # Skip DSPy compilation in test mode
            if os.environ.get("SKIP_DSPY_COMPILATION") == "true":
                app_logger.info("Skipping DSPy compilation (test mode)")
                self.app["compiled_http_program"] = None
            else:
                # Ensure the model name is set for DSPy configuration
                if not config.openai_model_name:
                    config.openai_model_name = "gpt-3.5-turbo"
                
                # Configure DSPy with environment variables for base URL if set
                base_url = os.environ.get("OPENAI_BASE_URL")
                if base_url:
                    dspy.configure(lm=dspy.LM(
                        f"openai/{config.openai_model_name}", 
                        base_url=base_url,
                        api_key=os.environ.get("OPENAI_API_KEY", config.api_key)
                    ))
                else:
                    dspy.configure(lm=dspy.LM(f"openai/{config.openai_model_name}"))
                    
                optimizer = BootstrapFewShot(metric=None, max_bootstrapped_demos=2)
                compiled_http_program = optimizer.compile(
                    HttpProgram(), trainset=training_data
                )
                self.app["compiled_http_program"] = compiled_http_program
                for server in mcp_servers:
                    if isinstance(server, MCPServerStdio):
                        server.global_state[
                            "compiled_http_program"
                        ] = compiled_http_program
                app_logger.info("DSPy program compiled successfully.")
        except Exception as e:
            app_logger.warning(
                f"DSPy compilation failed: {e}. Server will use fallback responses."
            )
            self.app["compiled_http_program"] = None

    def add_route(self, path: str, handler):
        self.app.router.add_route("*", path, handler)

    async def start(self):
        # Use config passed to constructor, or create new one from web_app_file
        if self.config:
            config = self.config
        elif self.web_app_file:
            config = Config(web_app_file=self.web_app_file)
        else:
            config = Config()

        self.app["config"] = config
        self.app["global_state"] = {}
        self.add_route("/{path:.*}", handle_http_request)
        await self.initialize_agent()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        app_logger.info(f"Web server started on http://{self.host}:{self.port}")
        if self.web_app_file:
            app_logger.info(f"Using web app file: {self.web_app_file}")

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    async def cleanup(self):
        await self.stop()
        for server in self.mcp_server_lifecycles:
            try:
                await server.__aexit__(None, None, None)
            except RuntimeError as e:
                # Suppress anyio cancel scope errors during cleanup
                # This happens when MCP client is cleaned up from a different task
                app_logger.debug(f"MCP server cleanup warning (safe to ignore): {e}")
        self.mcp_server_lifecycles.clear()