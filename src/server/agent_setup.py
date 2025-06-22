# MIT License
#
# Copyright (c) 2025 Timothy J Fontaine
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys

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
from aiohttp import web

from src.config import Config
from src.logging_config import get_loggers

# Get logger instance
app_logger, _, _ = get_loggers()


async def initialize_mcp_servers_and_agent(config: Config, app: web.Application):
    """
    Initialize MCP servers based on configuration and create an agent with them.
    Returns the configured agent.
    """
    mcp_servers = []
    app["mcp_server_lifecycles"] = []

    # Initialize external MCP servers from typed config
    if config.mcp_servers:
        app_logger.info(
            f"Initializing {len(config.mcp_servers)} external MCP server(s) using agents.mcp"
        )

        for mcp_cfg in config.mcp_servers:
            server_type = mcp_cfg.type.lower()
            server = None

            if server_type == "stdio":
                params = {
                    "command": mcp_cfg.command,
                    "args": mcp_cfg.args or [],
                    "cwd": mcp_cfg.cwd,
                    "env": mcp_cfg.env,
                }
                server = MCPServerStdio(
                    params=params, client_session_timeout_seconds=120
                )
            elif server_type == "sse":
                server = MCPServerSse(params={"url": mcp_cfg.url})
            elif server_type == "streamable_http":
                server = MCPServerStreamableHttp(params={"url": mcp_cfg.url})
            else:
                app_logger.error(f"Unsupported MCP server type: {server_type}")
                continue

            try:
                await server.__aenter__()
                app["mcp_server_lifecycles"].append(server)
                tools = await server.list_tools()

                if tools:
                    app_logger.info(f"Added and connected to MCP server: {server.name}")
                    tool_names = sorted(t.name for t in tools)
                    app_logger.info(f"  └─ Discovered tools: {tool_names}")
                    mcp_servers.append(server)
                else:
                    app_logger.warning(
                        f"MCP server {server.name} did not provide any tools and will be ignored."
                    )
                    await server.__aexit__(None, None, None)
                    app["mcp_server_lifecycles"].pop()
            except Exception as e:
                app_logger.exception(
                    f"Error initializing MCP server {server_type}: {e}"
                )

    # Spawn and connect to the local tools stdio server if enabled
    if config.local_tools_enabled:
        try:
            app_logger.info("Spawning local tools stdio server as a subprocess.")
            command_parts = [sys.executable, "main.py", "--local-tools-stdio"]
            params = {
                "command": command_parts[0],
                "args": command_parts[1:],
            }
            local_server_client = MCPServerStdio(
                params=params, client_session_timeout_seconds=120
            )

            await local_server_client.__aenter__()
            app["mcp_server_lifecycles"].append(local_server_client)
            tools = await local_server_client.list_tools()

            if tools:
                app_logger.info(
                    f"Successfully connected to local tools stdio server: {local_server_client.name}"
                )
                tool_names = sorted([t.name for t in tools])
                app_logger.info(f"  └─ Discovered tools: {tool_names}")
                mcp_servers.append(local_server_client)
            else:
                app_logger.warning(
                    f"Local tools stdio server {local_server_client.name} did not provide any tools and will be ignored."
                )
                await local_server_client.__aexit__(None, None, None)
                app["mcp_server_lifecycles"].pop()
        except Exception:
            app_logger.exception("Failed to spawn or connect to local tools server.")

    # Create model, using a custom client if a base_url is provided
    if config.openai_base_url:
        custom_client = AsyncOpenAI(
            api_key=config.api_key, base_url=config.openai_base_url
        )
        model = OpenAIChatCompletionsModel(
            model=config.openai_model_name, openai_client=custom_client
        )
        set_tracing_disabled(disabled=True)
        app_logger.info(
            f"Using custom OpenAI client with base_url: {config.openai_base_url}"
        )
        app_logger.info("Tracing disabled for custom provider")
    else:
        model = config.openai_model_name

    agent = Agent(
        name="HTTP LLM Server Agent",
        instructions="You are an LLM powering an HTTP server. Use available tools to enhance your responses when appropriate.",
        model=model,
        model_settings=ModelSettings(
            temperature=config.openai_temperature,
            include_usage=True,
        ),
        mcp_servers=mcp_servers,
    )

    app_logger.info(f"Agent initialized with {len(mcp_servers)} MCP server(s)")
    app["agent"] = agent
    return agent
