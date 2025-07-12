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
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

import aiofiles
import aiohttp
from mcp.server.fastmcp.server import Context
from mcp.server.fastmcp.server import FastMCP as Server
from mcp.types import CallToolResult, TextContent

from src.logging_config import configure_logging
from src.server.mcp_session import McpSessionStore

# A server instance is created globally, and tools are registered against it.
local_mcp_server = Server(
    "local-tools-server",
    title="Local Tools Server",
    description="Provides tools for session and state management.",
)


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

    # Prepare destination directory
    destination_dir = os.path.dirname(destination)
    if destination_dir:
        os.makedirs(destination_dir, exist_ok=True)
        logging.debug(f"Ensured directory exists: {destination_dir}")

    # Headers to make requests more reliable
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; http-llm-server/1.0)",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    # SSL context configuration
    connector = aiohttp.TCPConnector(
        limit=10,
        limit_per_host=3,
        ttl_dns_cache=300,
        use_dns_cache=True,
        ssl=False,  # Allow SSL certificate issues to be handled gracefully
    )

    timeout = aiohttp.ClientTimeout(total=600, connect=30, sock_read=30)

    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait_time = min(2**attempt, 60)  # Exponential backoff, max 60s
            logging.info(
                f"Retry attempt {attempt}/{max_retries} after {wait_time}s delay"
            )
            await asyncio.sleep(wait_time)

        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, headers=headers
            ) as session:
                logging.debug(f"Attempt {attempt + 1}: Making request to {url}")

                async with session.get(url, allow_redirects=True) as response:
                    logging.debug(f"HTTP {response.status}: {response.reason}")

                    # Handle HTTP errors
                    if response.status == 404:
                        raise ValueError(f"File not found (404): {url}")
                    elif response.status == 403:
                        raise ValueError(f"Access forbidden (403): {url}")
                    elif response.status == 429:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message="Rate limited",
                        )
                    elif response.status >= 500:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"Server error: {response.status}",
                        )

                    response.raise_for_status()

                    # Log response details
                    content_length = response.headers.get("Content-Length")
                    content_type = response.headers.get("Content-Type", "unknown")
                    logging.info(
                        f"Content-Type: {content_type}, "
                        f"Content-Length: {content_length}"
                    )

                    # Download file using async file operations
                    total_bytes = 0
                    chunk_size = 8192

                    try:
                        async with aiofiles.open(destination, "wb") as f:
                            async for chunk in response.content.iter_chunked(
                                chunk_size
                            ):
                                await f.write(chunk)
                                total_bytes += len(chunk)

                                # Log progress for large files
                                if (
                                    content_length and total_bytes % (1024 * 1024) == 0
                                ):  # Every MB
                                    progress = (total_bytes / int(content_length)) * 100
                                    logging.debug(
                                        f"Download progress: {progress:.1f}% "
                                        f"({total_bytes} bytes)"
                                    )

                        # Verify the download
                        if not os.path.exists(destination):
                            raise FileNotFoundError(
                                f"Downloaded file not found at {destination}"
                            )

                        file_size = os.path.getsize(destination)
                        if file_size == 0:
                            raise ValueError("Downloaded file is empty")

                        # Verify size if Content-Length was provided
                        if content_length and file_size != int(content_length):
                            logging.warning(
                                f"File size mismatch: expected {content_length}, "
                                f"got {file_size}"
                            )

                        logging.info(
                            "Download successful: %d bytes written to %s",
                            file_size,
                            destination,
                        )
                        return CallToolResult(
                            content=[
                                TextContent(
                                    type="text",
                                    text=(
                                        f"File downloaded to {destination} "
                                        f"({file_size:,} bytes, "
                                        f"Content-Type: {content_type})"
                                    ),
                                )
                            ]
                        )

                    except Exception as file_error:
                        # Clean up partial download
                        if os.path.exists(destination):
                            try:
                                os.remove(destination)
                                logging.debug(
                                    f"Cleaned up partial download: {destination}"
                                )
                            except OSError:
                                pass
                        raise file_error

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            last_exception = e
            error_type = type(e).__name__

            # Determine if this is retryable
            retryable = isinstance(
                e,
                (
                    aiohttp.ClientConnectionError,
                    aiohttp.ServerTimeoutError,
                    asyncio.TimeoutError,
                    aiohttp.ClientPayloadError,
                ),
            ) or (isinstance(e, aiohttp.ClientResponseError) and e.status >= 500)

            if not retryable or attempt == max_retries:
                logging.error(f"Download failed ({error_type}): {e}")
                # Clean up any partial download
                if os.path.exists(destination):
                    try:
                        os.remove(destination)
                        logging.debug(f"Cleaned up partial download: {destination}")
                    except OSError:
                        pass
                break
            else:
                logging.warning(f"Retryable error ({error_type}): {e}")

        except Exception as e:
            # Non-retryable errors
            last_exception = e
            logging.error(f"Non-retryable download error: {e}")
            # Clean up any partial download
            if os.path.exists(destination):
                try:
                    os.remove(destination)
                    logging.debug(f"Cleaned up partial download: {destination}")
                except OSError:
                    pass
            break

    # If we reach here, all retries failed
    error_msg = f"Failed to download file from {url} after {max_retries + 1} attempts"
    if last_exception:
        error_msg += f": {last_exception}"

    logging.error(error_msg)
    raise ValueError(error_msg)


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
    This is separate from HTTP conversation history and is meant for
    MCP-specific session state.
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
    If key is provided, returns the specific value. If key is empty, returns all
    session data as JSON.
    This is separate from HTTP conversation history and contains MCP-specific
    session state.
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
            "Local tool retrieved all MCP session data for %s: %d keys",
            session_id,
            len(session_data),
        )

    return CallToolResult(content=[TextContent(type="text", text=result_text)])


@local_mcp_server.tool()
async def delete_session_data(
    context: Context, session_id: str, key: str = ""
) -> CallToolResult:
    """
    Deletes MCP session data for the given session.
    If key is provided, deletes the specific key. If key is empty, deletes all
    session data.
    This is separate from HTTP conversation history and affects MCP-specific
    session state.
    """
    mcp_session_store: McpSessionStore = local_mcp_server.mcp_session_store

    if key:
        # Delete specific key
        existed = await mcp_session_store.delete_session_value(session_id, key)
        result_text = f"Key '{key}' {'deleted' if existed else 'not found'}."
        logging.info(
            "Local tool deleted MCP session data key: %s[%s] (existed: %s)",
            session_id,
            key,
            existed,
        )
    else:
        # Delete entire session
        existed = await mcp_session_store.delete_session(session_id)
        result_text = f"Session data {'deleted' if existed else 'not found'}."
        logging.info(
            "Local tool deleted all MCP session data: %s (existed: %s)",
            session_id,
            existed,
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
    Creates the StdioServer application for the local tools with separate MCP
    session store.
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


async def main():
    """Main function to run the MCP server with stdio transport."""
    log_level = os.environ.get("LOCAL_TOOLS_LOG_LEVEL", "INFO")
    configure_logging(log_level)

    logging.info("Starting local tools MCP server")

    try:
        # The tool implementations are registered with `local_mcp_server`
        # Now, we just need to run the server.
        # Initialize with empty global state and session store for standalone execution
        server = create_local_tools_stdio_server({}, McpSessionStore())
        logging.debug("Local tools initialized, starting stdio server")
        await server.run_stdio_async()
    except Exception as e:
        logging.error(f"Local tools failed to run: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
