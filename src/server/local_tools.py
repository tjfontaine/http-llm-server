import asyncio
import logging
import os
from typing import Any, Iterable

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..logging_config import configure_logging

# Global state and session store
global_state: dict[str, Any] = {}
mcp_session_store: dict[str, dict[str, Any]] = {}

# Create the core MCP server
server = Server("local-tools-server")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available local tools."""
    return [
        Tool(
            name="download_file",
            description="Download a file from a URL and save it locally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to download from",
                    },
                    "filename": {
                        "type": "string",
                        "description": "The local filename to save to",
                    },
                },
                "required": ["url", "filename"],
            },
        ),
        Tool(
            name="create_session",
            description="Create a new session with a unique ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional session ID to use",
                    },
                },
            },
        ),
        Tool(
            name="assign_session_id",
            description="Assign or retrieve a session ID for tracking conversations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional session ID to assign",
                    },
                },
            },
        ),
        Tool(
            name="get_global_state",
            description="Get a value from the global state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key to retrieve"},
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="set_global_state",
            description="Set a value in the global state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key to set"},
                    "value": {"description": "The value to set"},
                },
                "required": ["key", "value"],
            },
        ),
        Tool(
            name="get_session_data",
            description="Get data from a specific session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID"},
                    "key": {"type": "string", "description": "The key to retrieve"},
                },
                "required": ["session_id", "key"],
            },
        ),
        Tool(
            name="set_session_data",
            description="Set data for a specific session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID"},
                    "key": {"type": "string", "description": "The key to set"},
                    "value": {"description": "The value to set"},
                },
                "required": ["session_id", "key", "value"],
            },
        ),
        Tool(
            name="list_session_data",
            description="List all keys in a session's data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="delete_session_data",
            description="Delete a key from a session's data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID"},
                    "key": {"type": "string", "description": "The key to delete"},
                },
                "required": ["session_id", "key"],
            },
        ),
        Tool(
            name="clear_session_data",
            description="Clear all data for a session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID"},
                },
                "required": ["session_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> Iterable[TextContent]:
    """Handle tool calls."""
    try:
        if name == "download_file":
            url = arguments["url"]
            filename = arguments["filename"]

            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    content = await response.read()

            with open(filename, "wb") as f:
                f.write(content)

            return [
                TextContent(
                    type="text", text=f"Successfully downloaded {url} to {filename}"
                )
            ]

        elif name == "create_session":
            session_id = arguments.get("session_id")
            if not session_id:
                import uuid

                session_id = str(uuid.uuid4())

            if session_id not in mcp_session_store:
                mcp_session_store[session_id] = {}

            return [TextContent(type="text", text=f"Created session: {session_id}")]

        elif name == "assign_session_id":
            session_id = arguments.get("session_id")
            if not session_id:
                import uuid

                session_id = str(uuid.uuid4())

            if session_id not in mcp_session_store:
                mcp_session_store[session_id] = {}

            return [TextContent(type="text", text=f"Assigned session ID: {session_id}")]

        elif name == "get_global_state":
            key = arguments["key"]
            value = global_state.get(key)
            return [TextContent(type="text", text=f"Global state[{key}] = {value}")]

        elif name == "set_global_state":
            key = arguments["key"]
            value = arguments["value"]
            global_state[key] = value
            return [TextContent(type="text", text=f"Set global state[{key}] = {value}")]

        elif name == "get_session_data":
            session_id = arguments["session_id"]
            key = arguments["key"]

            if session_id not in mcp_session_store:
                return [
                    TextContent(type="text", text=f"Session {session_id} not found")
                ]

            value = mcp_session_store[session_id].get(key)
            return [
                TextContent(type="text", text=f"Session {session_id}[{key}] = {value}")
            ]

        elif name == "set_session_data":
            session_id = arguments["session_id"]
            key = arguments["key"]
            value = arguments["value"]

            if session_id not in mcp_session_store:
                mcp_session_store[session_id] = {}

            mcp_session_store[session_id][key] = value
            return [
                TextContent(
                    type="text", text=f"Set session {session_id}[{key}] = {value}"
                )
            ]

        elif name == "list_session_data":
            session_id = arguments["session_id"]

            if session_id not in mcp_session_store:
                return [
                    TextContent(type="text", text=f"Session {session_id} not found")
                ]

            keys = list(mcp_session_store[session_id].keys())
            return [TextContent(type="text", text=f"Session {session_id} keys: {keys}")]

        elif name == "delete_session_data":
            session_id = arguments["session_id"]
            key = arguments["key"]

            if session_id not in mcp_session_store:
                return [
                    TextContent(type="text", text=f"Session {session_id} not found")
                ]

            if key in mcp_session_store[session_id]:
                del mcp_session_store[session_id][key]
                return [
                    TextContent(
                        type="text", text=f"Deleted session {session_id}[{key}]"
                    )
                ]
            else:
                return [
                    TextContent(
                        type="text", text=f"Key {key} not found in session {session_id}"
                    )
                ]

        elif name == "clear_session_data":
            session_id = arguments["session_id"]

            if session_id not in mcp_session_store:
                return [
                    TextContent(type="text", text=f"Session {session_id} not found")
                ]

            mcp_session_store[session_id].clear()
            return [
                TextContent(
                    type="text", text=f"Cleared all data for session {session_id}"
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logging.error(f"Error in tool {name}: {e}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Main entry point for the local tools server."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    configure_logging(log_level)

    logging.info("Starting local tools MCP server...")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
