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

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp.server import Context
from mcp.server.fastmcp.server import FastMCP as Server
from mcp.types import CallToolResult, TextContent

from src.server.models import ConversationHistory
from src.server.session import AbstractSessionStore


# A server instance is created globally, and tools are registered against it.
local_mcp_server = Server(
    "local-tools-server",
    title="Local Tools Server",
    description="Provides tools for session and state management.",
)


@local_mcp_server.tool()
async def create_session(context: Context) -> CallToolResult:
    """
    Creates a new, unique session identifier.
    """
    new_id = str(uuid.uuid4())
    logging.info(f"Local tool created new session: {new_id}")
    return CallToolResult(content=[TextContent(type="text", text=new_id)])


@local_mcp_server.tool()
async def assign_session_id(context: Context, session_id: str) -> CallToolResult:
    """
    Assigns or confirms the session ID for the current request.
    This tool should be called by the LLM to signal which session it is operating on,
    especially when creating a new session. The ID is then used by the server
    to correctly associate conversation history.
    """
    logging.info(f"Local tool assign_session_id called with: {session_id}")
    return CallToolResult(
        content=[TextContent(type="text", text=f"Session ID assigned: {session_id}")]
    )


@local_mcp_server.tool()
async def set_global_state(context: Context, key: str, value: str) -> CallToolResult:
    """
    Stores a string value in a global, server-side dictionary.
    """
    global_state = local_mcp_server.global_state
    global_state[key] = value
    logging.info(f"Local tool set global state: {key} = {value}")
    return CallToolResult(
        content=[TextContent(type="text", text=f"Value for '{key}' has been set.")]
    )


@local_mcp_server.tool()
async def get_global_state(context: Context, key: str) -> CallToolResult:
    """
    Retrieves a string value from the global, server-side dictionary.
    """
    global_state = local_mcp_server.global_state
    value = global_state.get(key, "")
    logging.info(f"Local tool retrieved global state: {key} -> {value}")
    return CallToolResult(content=[TextContent(type="text", text=value)])


@local_mcp_server.tool()
async def get_conversation_history(context: Context, session_id: str) -> CallToolResult:
    """
    Retrieves the full, ordered conversation history for a given session ID.
    """
    session_store: AbstractSessionStore = local_mcp_server.session_store
    history = await session_store.get_history(session_id)
    return CallToolResult(
        content=[TextContent(type="text", text=history.model_dump_json(indent=2))]
    )


@local_mcp_server.tool()
async def update_session_history(
    context: Context, session_id: str, new_history_json: str
) -> CallToolResult:
    """
    Replaces the entire conversation history for a session with a new one.
    """
    session_store: AbstractSessionStore = local_mcp_server.session_store
    try:
        new_history = ConversationHistory.model_validate_json(new_history_json)
        await session_store.replace_history(session_id, new_history)
        return CallToolResult(
            content=[
                TextContent(
                    type="text", text="Conversation history replaced successfully."
                )
            ]
        )
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"Invalid history format: {e}") from e


def create_local_tools_stdio_server(
    global_state: dict, session_store: AbstractSessionStore
) -> Server:
    """
    Creates the StdioServer application for the local tools.
    """
    local_mcp_server.global_state = global_state
    local_mcp_server.session_store = session_store

    @asynccontextmanager
    async def lifespan(server_instance: Server) -> AsyncIterator[dict]:
        """Manage server startup and shutdown, yielding state to the context."""
        # State is attached directly to the server instance for stdio transport.
        yield

    local_mcp_server.lifespan = lifespan
    return local_mcp_server
