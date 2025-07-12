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

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiofiles
import aiohttp
from mcp.server.fastmcp.server import Context
from mcp.server.fastmcp.server import FastMCP as Server
from mcp.types import CallToolResult, TextContent

from src.logging_config import configure_logging

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
    This tool is called by the LLM to signal which session it is operating on.
    With the new session management, this is mostly a no-op for compatibility.
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
async def download_file(
    context: Context, url: str, destination: str, max_retries: int = 3
) -> CallToolResult:
    """
    Downloads a file from a URL to a local destination, following redirects.
    Includes retry logic, proper headers, and robust error handling.

    Args:
        url: The URL to download from
        destination: Local file path to save to
        max_retries: Maximum number of retry attempts (default: 3)
    """
    logging.info(
        f"START: url={url}, destination={destination}, max_retries={max_retries}"
    )

    destination_dir = os.path.dirname(destination)
    if destination_dir:
        os.makedirs(destination_dir, exist_ok=True)
        logging.debug(f"Ensured directory exists: {destination_dir}")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; http-llm-server/1.0)",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    connector = aiohttp.TCPConnector(limit_per_host=5)
    timeout = aiohttp.ClientTimeout(total=600, connect=30, sock_read=300)
    last_exception = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait_time = min(2**attempt, 30)  # Exponential backoff
            logging.info(
                f"Retry attempt {attempt}/{max_retries} after {wait_time}s delay"
            )
            await asyncio.sleep(wait_time)

        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, headers=headers
            ) as session:
                async with session.get(url, allow_redirects=True) as response:
                    response.raise_for_status()

                    content_length = response.headers.get("Content-Length")
                    logging.info(
                        f"Downloading from {response.url} "
                        f"({response.status}, {content_length} bytes)"
                    )

                    total_bytes = 0
                    async with aiofiles.open(destination, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                            total_bytes += len(chunk)

                    logging.info(
                        "Download successful: %d bytes written to %s",
                        total_bytes,
                        destination,
                    )
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=(
                                    f"File downloaded to {destination} "
                                    f"({total_bytes:,} bytes)"
                                ),
                            )
                        ]
                    )

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            last_exception = e
            logging.warning(f"Download attempt {attempt + 1} failed: {e}")
            if attempt == max_retries:
                logging.error("Final download attempt failed.")
                break
            continue

    error_msg = f"Failed to download file from {url} after {max_retries + 1} attempts"
    if last_exception:
        error_msg += f": {last_exception}"

    raise ValueError(error_msg)


def create_local_tools_stdio_server(
    global_state: dict,
) -> Server:
    """
    Creates the StdioServer application for the local tools with separate MCP
    session store.
    """
    local_mcp_server.global_state = global_state

    @asynccontextmanager
    async def lifespan(server_instance: Server) -> AsyncIterator[dict]:
        """Manage server startup and shutdown, yielding state to the context."""
        # State is attached directly to the server instance for stdio transport.
        yield

    local_mcp_server.lifespan = lifespan
    return local_mcp_server


async def main():
    """Main function to run the MCP server with stdio transport."""
    log_level = os.environ.get("LOCAL_TOOLS_LOG_LEVEL", "INFO")
    configure_logging(log_level)

    logging.info("Starting local tools MCP server")

    try:
        # The tool implementations are registered with `local_mcp_server`
        # Now, we just need to run the server.
        # Initialize with empty global state and session store for standalone execution
        server = create_local_tools_stdio_server({})
        logging.debug("Local tools initialized, starting stdio server")
        await server.run_stdio_async()
    except Exception as e:
        logging.error(f"Local tools failed to run: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
