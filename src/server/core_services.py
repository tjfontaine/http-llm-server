import asyncio
import logging
import os
import re
import sys
import uuid
from datetime import datetime

import jinja2
import yaml
from mcp.server.fastmcp.server import Context, FastMCP
from mcp.types import TextContent
from rich.console import Console
from rich.logging import RichHandler

# Note: WebServer is imported lazily inside functions to prevent
# module-level configure_logging from writing to stdout before
# core_services sets up stderr logging.


logger = logging.getLogger("llm_http_server_app")


def configure_subprocess_logging(log_level: str = "INFO"):
    """Configure consistent logging for subprocess visibility."""
    # Import the filter from the main logging configuration
    from src.logging_config import SingleLineExtrasFilter

    # Get the root logger
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.setLevel(log_level)
    else:
        # Clear existing handlers to avoid duplicate logs
        root_logger.handlers.clear()

    # Create console that explicitly writes to stderr (not stdout, to avoid MCP
    # protocol interference)
    console = Console(file=sys.stderr, force_terminal=True)

    # Add RichHandler with same configuration as main process, but with explicit
    # stderr console
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        show_path=False,
    )
    rich_handler.addFilter(SingleLineExtrasFilter())
    root_logger.addHandler(rich_handler)


# In-memory store for our web server resources
web_servers: dict[str, dict] = {}

core_services = FastMCP("core-services")


@core_services.tool()
async def create_web_resource(
    context: Context,
    port: int,
    host: str = "localhost",
    mcp_servers: list = [],
    log_level: str = "INFO",
    web_app_file: str = None,
) -> TextContent:
    """Creates a web resource and returns its unique ID."""
    # Lazy import to prevent module-level logging before stderr is configured
    from src.server.web_resource import WebServer
    
    logger.debug(f"create_web_resource called with mcp_servers={mcp_servers}")
    try:
        logger.debug(
            f"Creating web resource: port={port}, host={host}, "
            f"mcp_servers={len(mcp_servers)} servers, log_level={log_level}, "
            f"web_app_file={web_app_file}"
        )

        resource_id = str(uuid.uuid4())
        server_resource = WebServer(
            port=port,
            host=host,
            mcp_servers_config=mcp_servers,
            log_level=log_level,
            web_app_file=web_app_file,
        )

        # Store with metadata for better tracking
        web_servers[resource_id] = {
            "id": resource_id,
            "resource": server_resource,
            "port": port,
            "host": host,
            "mcp_servers": [
                server["label"] for server in mcp_servers if "label" in server
            ],
            "status": "created",
            "created": datetime.now().isoformat(),
            "log_level": log_level,
            "web_app_file": web_app_file,
        }

        success_msg = f"Web resource created with ID: {resource_id}"
        logger.info(success_msg)
        return TextContent(type="text", text=success_msg)

    except Exception as e:
        error_msg = f"Failed to create web resource: {e}"
        logger.exception(error_msg)
        raise ValueError(error_msg)


@core_services.tool()
async def start_web_resource(context: Context, resource_id: str) -> TextContent:
    """Starts a created web resource by its ID."""
    if resource_id not in web_servers:
        raise ValueError(f"Web resource with ID {resource_id} not found")

    server_resource = web_servers[resource_id]
    if server_resource["status"] == "running":
        return TextContent(
            type="text", text=f"Web server {resource_id} is already running."
        )

    web_server = server_resource["resource"]

    async def start_server_task():
        try:
            await web_server.start()
            web_servers[resource_id]["status"] = "running"
            web_servers[resource_id]["started"] = datetime.now().isoformat()
            logger.info(
                f"Web server {resource_id} started successfully on "
                f"{server_resource['host']}:{server_resource['port']}"
            )
        except Exception as e:
            web_servers[resource_id]["status"] = "error"
            web_servers[resource_id]["error"] = str(e)
            logger.exception(f"Failed to start web server {resource_id}: {e}")

    # Start server in a background task
    asyncio.create_task(start_server_task())
    # Give it a moment to initialize
    await asyncio.sleep(0.2)

    success_msg = (
        f"Web server {resource_id} startup initiated on "
        f"{server_resource['host']}:{server_resource['port']}."
    )
    return TextContent(type="text", text=success_msg)


@core_services.tool()
async def stop_web_resource(context: Context, resource_id: str) -> TextContent:
    """Stops a running web resource by its ID."""
    if resource_id not in web_servers:
        raise ValueError(f"Web resource with ID {resource_id} not found")

    server_resource = web_servers[resource_id]
    if server_resource["status"] != "running":
        return TextContent(
            type="text", text=f"Web server {resource_id} is not running."
        )

    web_server = server_resource["resource"]
    await web_server.stop()
    web_servers[resource_id]["status"] = "stopped"
    web_servers[resource_id]["stopped"] = datetime.now().isoformat()
    logger.info(f"Web server {resource_id} stopped successfully")
    return TextContent(type="text", text=f"Web server {resource_id} stopped.")


@core_services.tool()
async def setup_web_application(
    context: Context,
    web_app_file: str,
    port: int = 8080,
    enable_local_tools: bool = True,
    log_level: str = "INFO",
) -> TextContent:
    """
    Sets up the web application environment by creating and starting a WebServer.
    This is a high-level tool that combines multiple steps into one operation.
    """
    logger.debug(f"setup_web_application called with web_app_file={web_app_file}, port={port}, enable_local_tools={enable_local_tools}, log_level={log_level}")
    resource_id = None
    try:
        if not os.path.exists(web_app_file):
            raise FileNotFoundError(f"Web app file not found: {web_app_file}")

        if not isinstance(enable_local_tools, bool):
            raise ValueError(
                f"Invalid enable_local_tools: {type(enable_local_tools)}. "
                "Must be a boolean."
            )

        mcp_servers = []
        if enable_local_tools:
            logger.debug("Local tools enabled, adding to MCP servers")
            # This configuration points to the local tools stdio server
            project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..")
            )
            mcp_servers.append(
                {
                    "type": "stdio",
                    "cwd": project_root,
                    "module": "src.server.local_tools",
                }
            )

        # Check for YAML front matter for additional MCP servers
        logger.debug(f"Reading web app file: {web_app_file}")
        with open(web_app_file, "r") as f:
            content = f.read()

        web_app_dir = os.path.dirname(os.path.abspath(web_app_file))
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )

        template = jinja2.Template(content)
        rendered_content = template.render(
            WEB_APP_DIR=web_app_dir, project_root=project_root
        )

        logger.debug(
            f"Web app file content starts with: {rendered_content[:100]}..."
        )
        if rendered_content.startswith("---"):
            parts = rendered_content.split("---", 2)
            if len(parts) > 2:
                front_matter_str = parts[1]
                front_matter = yaml.safe_load(front_matter_str)
                if "mcp_servers" in front_matter:
                    additional_servers = front_matter["mcp_servers"]
                    if isinstance(additional_servers, list):
                        logger.debug(
                            f"Found {len(additional_servers)} additional MCP "
                            "servers in web app file"
                        )
                        mcp_servers.extend(additional_servers)
                    else:
                        logger.warning(
                            "mcp_servers in YAML front matter is not a list, "
                            "skipping"
                        )

        # Now, create and start the web resource with the collected MCP servers
        create_result = await create_web_resource(
            context,
            port=port,
            mcp_servers=mcp_servers,
            log_level=log_level,
            web_app_file=web_app_file,
        )
        resource_id_str = create_result.text
        resource_id_match = re.search(r"[a-f0-9-]+$", resource_id_str)
        if not resource_id_match:
            raise ValueError(f"Could not extract resource ID from: {resource_id_str}")
        resource_id = resource_id_match.group(0)

        # Start the resource
        await start_web_resource(context, resource_id)

        # Return a summary
        final_status = web_servers[resource_id]["status"]
        final_url = f"http://{web_servers[resource_id]['host']}:{port}"
        return TextContent(
            type="text",
            text=(
                f"Web application '{os.path.basename(web_app_file)}' "
                f"setup complete. Status: {final_status}. URL: {final_url}"
            ),
        )

    except Exception:
        logger.exception(f"Failed to setup web application from '{web_app_file}'")
        if resource_id and resource_id in web_servers:
            await stop_web_resource(context, resource_id)
        raise


@core_services.tool()
async def list_web_resources(context: Context) -> TextContent:
    """Lists all created web server resources and their status."""
    if not web_servers:
        return TextContent(type="text", text="No web server resources found.")

    resource_list = [
        {
            "id": data["id"],
            "status": data["status"],
            "host": data["host"],
            "port": data["port"],
            "mcp_servers": data["mcp_servers"],
            "created": data["created"],
            "started": data.get("started"),
            "stopped": data.get("stopped"),
            "error": data.get("error"),
        }
        for data in web_servers.values()
    ]

    result = f"Found {len(web_servers)} web server resource(s):\n"
    for resource in resource_list:
        result += (
            f"  - {resource['id']}: {resource['status']} on "
            f"{resource['host']}:{resource['port']}\n"
        )
        result += (
            f"    MCP servers: {resource['mcp_servers']}, "
            f"Created: {resource['created']}\n"
        )
        if "started" in resource:
            result += f"    Started: {resource['started']}\n"
        if "stopped" in resource:
            result += f"    Stopped: {resource['stopped']}\n"
        if "error" in resource:
            result += f"    Error: {resource['error']}\n"

    return TextContent(type="text", text=result)


async def main():
    """Main function to run the core services server."""
    log_level = os.environ.get("CORE_SERVICES_LOG_LEVEL", "INFO")
    configure_subprocess_logging(log_level)
    logger.info("Starting core services MCP server via stdio")
    await core_services.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
