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

# A server instance is created globally, and tools are registered against it.
local_mcp_server = Server(
    name="local-tools-server",
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

@local_mcp_server.tool()
async def generate_http_response(
    context: Context, context_str: str, http_request: str
) -> CallToolResult:
    """
    Generates an HTTP response using the compiled DSPy program.
    
    Args:
        context_str: The context string for the request (e.g., global state info)
        http_request: The raw HTTP request string
    """
    import os
    from src.dspy_module import HttpProgram
    
    logging.info("generate_http_response tool called!")
    logging.info(f"context_str: {context_str}")
    logging.info(f"http_request: {http_request}")
    
    # Try to load the compiled DSPy program from file
    # The program is saved by web_resource.py after compilation
    compiled_program = None
    dspy_program_path = os.path.join(os.getcwd(), "data", ".dspy_cache", "http_program.json")
    
    if os.path.exists(dspy_program_path):
        try:
            compiled_program = HttpProgram()
            compiled_program.load(dspy_program_path)
            logging.info(f"Loaded compiled DSPy program from {dspy_program_path}")
        except Exception as e:
            logging.error(f"Failed to load DSPy program: {e}")
            compiled_program = None
    else:
        logging.info(f"No compiled DSPy program found at {dspy_program_path}")
    
    if compiled_program:
        try:
            # Use the compiled DSPy program with parameter names matching signature
            result = compiled_program(context=context_str, http_request=http_request)
            http_response = result.http_response
            logging.info(f"DSPy generated response: {http_response}")
            return CallToolResult(
                content=[TextContent(type="text", text=http_response)]
            )
        except Exception as e:
            logging.error(f"Error running DSPy program: {e}")
            # Fall through to fallback
    
    # Fallback response if DSPy program is not available or failed
    fallback_response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Hello, world!"
    )
    logging.info(f"Using fallback response: {fallback_response}")
    return CallToolResult(
        content=[TextContent(type="text", text=fallback_response)]
    )

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
    # MCP stdio transport uses stdout for JSONRPC - logging MUST go to stderr
    import sys
    log_level = os.environ.get("LOCAL_TOOLS_LOG_LEVEL", "INFO")
    
    # Configure basic logging to stderr (not stdout) to avoid corrupting JSONRPC
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logging.root.handlers.clear()
    logging.root.addHandler(handler)
    logging.root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

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