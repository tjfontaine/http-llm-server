from aiohttp import web
from typing import Coroutine, Callable, Any

from src.app import handle_http_request
from src.logging_config import get_loggers
from src.server.middleware import (
    error_handling_middleware,
    logging_and_metrics_middleware,
    session_cleanup_middleware,
    session_middleware,
)
from src.server.conversation import HttpConversationStore
from src.config import Config, McpServerConfig
from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)
from agents.mcp import (
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.model_settings import ModelSettings
import sys


app_logger, _, _ = get_loggers()


class WebServer:
    """A wrapper for the aiohttp web server."""

    def __init__(
        self,
        port: int,
        host: str,
        mcp_servers_config: list = [],
        core_services_server_obj=None,
    ):
        self.port = port
        self.host = host
        self.mcp_servers_config = mcp_servers_config
        self.core_services_server_obj = core_services_server_obj
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

    def add_route(
        self, path: str, handler: Callable[[web.Request], Coroutine[Any, Any, Any]]
    ):
        """Adds a route to the web application."""
        self.app.router.add_route("*", path, handler)

    async def initialize_agent(self, config: Config):
        """
        Initialize MCP servers based on configuration and create an agent with them.
        """
        app_logger.debug("Starting MCP servers initialization")
        mcp_servers = []

        # Add the core services server object if it was provided
        if self.core_services_server_obj:
            app_logger.debug("Adding core_services tools to the agent.")
            mcp_servers.append(self.core_services_server_obj)

        # Initialize external MCP servers from typed config
        if self.mcp_servers_config:
            app_logger.debug(
                f"Initializing {len(self.mcp_servers_config)} MCP server(s)"
            )

            for i, mcp_cfg_dict in enumerate(self.mcp_servers_config):
                app_logger.debug(
                    f"Processing MCP server {i + 1}/{len(self.mcp_servers_config)}: {mcp_cfg_dict}"
                )

                try:
                    mcp_cfg = McpServerConfig(**mcp_cfg_dict)
                    server_type = mcp_cfg.type.lower()
                    server = None

                    if server_type == "stdio":
                        # Handle simplified stdio config that just specifies a module
                        if "module" in mcp_cfg_dict:
                            app_logger.debug(
                                f"Using module-based stdio config: {mcp_cfg_dict['module']}"
                            )
                            params = {
                                "command": sys.executable,
                                "args": ["-m", mcp_cfg_dict["module"]],
                            }
                        else:
                            app_logger.debug("Using full stdio config")
                            # Handle full stdio config
                            params = {
                                "command": mcp_cfg.command,
                                "args": mcp_cfg.args or [],
                                "cwd": mcp_cfg.cwd,
                                "env": mcp_cfg.env,
                            }

                        server = MCPServerStdio(params=params)
                    elif server_type == "sse":
                        app_logger.debug(f"Creating SSE server with URL: {mcp_cfg.url}")
                        server = MCPServerSse(params={"url": mcp_cfg.url})
                    elif server_type == "streamable_http":
                        app_logger.debug(
                            f"Creating StreamableHttp server with URL: {mcp_cfg.url}"
                        )
                        server = MCPServerStreamableHttp(params={"url": mcp_cfg.url})
                    else:
                        app_logger.error(f"Unsupported MCP server type: {server_type}")
                        continue

                    # Initialize server
                    await server.__aenter__()
                    self.mcp_server_lifecycles.append(server)

                    tools = await server.list_tools()
                    app_logger.debug(
                        f"Server {server.name} provides {len(tools) if tools else 0} tools"
                    )

                    if tools:
                        tool_names = sorted(t.name for t in tools)
                        app_logger.info(
                            f"Connected to MCP server: {server.name} ({len(tools)} tools)"
                        )
                        app_logger.debug(f"Available tools: {tool_names}")
                        mcp_servers.append(server)
                    else:
                        app_logger.warning(
                            f"MCP server {server.name} provided no tools, ignoring"
                        )
                        await server.__aexit__(None, None, None)
                        self.mcp_server_lifecycles.pop()

                except Exception as e:
                    app_logger.error(
                        f"Failed to initialize MCP server {server_type}: {e}",
                        exc_info=True,
                    )
        else:
            app_logger.debug("No external MCP servers configured")

        # Create model
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
            instructions=None,  # Set to None so system prompt from messages takes precedence
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

    async def start(self):
        """Starts the web server."""
        app_logger.debug("Starting web server")

        if self.runner is not None:
            app_logger.warning("Server is already running")
            return

        # Create server configuration
        app_logger.debug("Setting up web application")
        config = Config()
        self.app["config"] = config
        self.app["session_store"] = HttpConversationStore(
            save_to_disk=config.save_conversations
        )
        self.app["global_state"] = {}
        self.app["error_llm_system_prompt_template"] = (
            config.error_llm_system_prompt_template
        )
        self.app["web_app_rules"] = config.web_app_rules

        # Add the main route
        self.add_route("/{path:.*}", handle_http_request)

        # Initialize agent with MCP servers
        app_logger.debug("Initializing agent")
        await self.initialize_agent(config)

        # Set up aiohttp runner
        app_logger.debug("Setting up HTTP server")
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        app_logger.info(f"Web server started on http://{self.host}:{self.port}")

    async def stop(self):
        """Stops the web server gracefully."""
        app_logger.debug("Stopping web server")
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        app_logger.info("Web server stopped")

    async def cleanup(self):
        """Clean up resources including MCP servers."""
        app_logger.debug("Cleaning up web server resources")
        await self.stop()

        # Clean up MCP servers
        for server in self.mcp_server_lifecycles:
            try:
                await server.__aexit__(None, None, None)
            except Exception as e:
                app_logger.error(f"Error cleaning up MCP server: {e}")

        self.mcp_server_lifecycles.clear()
        app_logger.debug("Web server cleanup complete")
