import asyncio
import logging

from mcp.server.fastmcp.server import FastMCP as Server, Context
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

server = Server(
    "core-services",
    title="Core Services",
    description="Provides core services for the application.",
)


@server.tool()
async def read_file(context: Context, path: str) -> TextContent:
    """Reads the entire content of a file and returns it as a string."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return TextContent(type="text", text=content)
    except FileNotFoundError:
        # NOTE: We can't return an ErrorContent here because the return type
        # is TextContent. The framework will catch the exception and handle it.
        raise
    except Exception:
        raise


def main():
    logger.info("Starting core-services MCP server...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main() 