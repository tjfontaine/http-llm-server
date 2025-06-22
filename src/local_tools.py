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
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
from mcp.server.fastmcp.server import Context
from mcp.server.fastmcp.server import FastMCP as Server
from mcp.types import CallToolResult, TextContent

from src.server.mcp_session import McpSessionStore


# A server instance is created globally, and tools are registered against it.
local_mcp_server = Server(
    "local-tools-server",
    title="Local Tools Server",
    description="Provides tools for session and state management.",
)


@local_mcp_server.tool()
async def download_file(context: Context, url: str, destination: str) -> CallToolResult:
    """
    Downloads a file from a URL to a local destination, following redirects.
    """
    logging.info(f"START: url={url}, destination={destination}")
    try:
        destination_dir = os.path.dirname(destination)
        if destination_dir:
            os.makedirs(destination_dir, exist_ok=True)
            logging.debug(f"Ensured directory exists: {destination_dir}")
        async with aiohttp.ClientSession() as session:
            logging.debug("ClientSession created")
            async with session.get(url, allow_redirects=True, timeout=300) as response:
                logging.debug(f"HTTP GET status: {response.status}")
                response.raise_for_status()
                with open(destination, "wb") as f:
                    total = 0
                    while True:
                        chunk = await response.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
                    logging.debug(f"Wrote {total} bytes to {destination}")
        file_size = os.path.getsize(destination)
        logging.info(f"Download successful. File size: {file_size} bytes")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"File downloaded successfully to {destination} ({file_size} bytes)",
                )
            ]
        )
    except Exception as e:
        logging.error(f"Failed to download file from {url}: {e}")
        raise ValueError(f"Failed to download file from {url}: {e}")


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
async def set_session_data(
    context: Context, session_id: str, key: str, value: str
) -> CallToolResult:
    """
    Stores a key-value pair in the MCP session data for the given session.
    This is separate from HTTP conversation history and is meant for MCP-specific session state.
    """
    mcp_session_store: McpSessionStore = local_mcp_server.mcp_session_store
    await mcp_session_store.set_session_value(session_id, key, value)
    logging.info(f"Local tool set MCP session data: {session_id}[{key}] = {value}")
    return CallToolResult(
        content=[TextContent(type="text", text=f"Session data set for key '{key}'.")]
    )


@local_mcp_server.tool()
async def get_session_data(
    context: Context, session_id: str, key: str = ""
) -> CallToolResult:
    """
    Retrieves MCP session data for the given session.
    If key is provided, returns the specific value. If key is empty, returns all session data as JSON.
    This is separate from HTTP conversation history and contains MCP-specific session state.
    """
    mcp_session_store: McpSessionStore = local_mcp_server.mcp_session_store

    if key:
        # Get specific key
        value = await mcp_session_store.get_session_value(session_id, key)
        result_text = str(value) if value is not None else ""
        logging.info(
            f"Local tool retrieved MCP session data: {session_id}[{key}] -> {value}"
        )
    else:
        # Get all session data
        session_data = await mcp_session_store.get_session_data(session_id)
        result_text = json.dumps(session_data, indent=2)
        logging.info(
            f"Local tool retrieved all MCP session data for {session_id}: {len(session_data)} keys"
        )

    return CallToolResult(content=[TextContent(type="text", text=result_text)])


@local_mcp_server.tool()
async def delete_session_data(
    context: Context, session_id: str, key: str = ""
) -> CallToolResult:
    """
    Deletes MCP session data for the given session.
    If key is provided, deletes the specific key. If key is empty, deletes all session data.
    This is separate from HTTP conversation history and affects MCP-specific session state.
    """
    mcp_session_store: McpSessionStore = local_mcp_server.mcp_session_store

    if key:
        # Delete specific key
        existed = await mcp_session_store.delete_session_value(session_id, key)
        result_text = f"Key '{key}' {'deleted' if existed else 'not found'}."
        logging.info(
            f"Local tool deleted MCP session data key: {session_id}[{key}] (existed: {existed})"
        )
    else:
        # Delete entire session
        existed = await mcp_session_store.delete_session(session_id)
        result_text = f"Session data {'deleted' if existed else 'not found'}."
        logging.info(
            f"Local tool deleted all MCP session data: {session_id} (existed: {existed})"
        )

    return CallToolResult(content=[TextContent(type="text", text=result_text)])


@local_mcp_server.tool()
async def list_sessions(context: Context) -> CallToolResult:
    """
    Lists all session IDs that have MCP session data.
    This shows sessions from the MCP perspective, not HTTP conversation history.
    """
    mcp_session_store: McpSessionStore = local_mcp_server.mcp_session_store
    session_ids = await mcp_session_store.list_sessions()
    result_text = (
        json.dumps(session_ids, indent=2) if session_ids else "No MCP sessions found."
    )

    logging.info(f"Local tool listed MCP sessions: {len(session_ids)} sessions")
    return CallToolResult(content=[TextContent(type="text", text=result_text)])


def create_local_tools_stdio_server(
    global_state: dict, mcp_session_store: McpSessionStore
) -> Server:
    """
    Creates the StdioServer application for the local tools with separate MCP session store.
    """
    local_mcp_server.global_state = global_state
    local_mcp_server.mcp_session_store = mcp_session_store

    @asynccontextmanager
    async def lifespan(server_instance: Server) -> AsyncIterator[dict]:
        """Manage server startup and shutdown, yielding state to the context."""
        # State is attached directly to the server instance for stdio transport.
        yield

    local_mcp_server.lifespan = lifespan
    return local_mcp_server
