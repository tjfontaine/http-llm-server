import logging
import asyncio
import os
import sys
import uuid

from mcp.server.fastmcp.server import FastMCP as Server, Context
from mcp.types import TextContent
from src.config import Config
from src.server.web_resource import WebServer

logger = logging.getLogger(__name__)


def configure_subprocess_logging(log_level: str = "INFO"):
    """Configure consistent logging for subprocess visibility matching main process format."""
    # Import both the formatter and RichHandler from main process
    from src.logging_config import EnhancedStructuredFormatter
    from rich.logging import RichHandler
    from rich.console import Console

    # Convert string log level to logging constant
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure root logger with consistent formatter
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Create console that explicitly writes to stderr (not stdout, to avoid MCP protocol interference)
    console = Console(file=sys.stderr, force_terminal=True)

    # Add RichHandler with same configuration as main process, but with explicit stderr console
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_path=False,
        show_time=False,
        markup=True,
    )
    rich_handler.setLevel(level)
    rich_handler.setFormatter(EnhancedStructuredFormatter())
    root_logger.addHandler(rich_handler)

    # Configure specific loggers
    noisy_deps = ["urllib3", "httpcore", "httpx", "aiohttp", "openai", "agents", "mcp"]
    for dep_name in noisy_deps:
        logging.getLogger(dep_name).setLevel(logging.WARNING)

    app_loggers = [
        "llm_http_server_app",
        "http_access",
        "conversation_history",
        "__main__",
    ]
    for logger_name in app_loggers:
        logger_instance = logging.getLogger(logger_name)
        logger_instance.setLevel(level)
        logger_instance.propagate = True


# In-memory store for our web server resources
web_servers: dict[str, WebServer] = {}

core_services = Server(
    "core-services",
    title="Core Services",
    description="Provides core services for setting up and managing web application resources.",
)


@core_services.tool()
async def create_web_resource(
    context: Context, port: int, host: str = "0.0.0.0", mcp_servers: list = []
) -> TextContent:
    """Creates a new web server resource and returns its unique ID."""
    try:
        logger.debug(
            f"Creating web resource: port={port}, host={host}, mcp_servers={len(mcp_servers)} servers"
        )

        # Validate inputs
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(
                f"Invalid port number: {port}. Must be between 1 and 65535."
            )

        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"Invalid host: {host}. Must be a non-empty string.")

        if not isinstance(mcp_servers, list):
            raise ValueError(
                f"Invalid mcp_servers: {type(mcp_servers)}. Must be a list."
            )

        resource_id = str(uuid.uuid4())
        logger.debug(f"Generated resource ID: {resource_id}")

        server_instance = WebServer(
            port=port, host=host, mcp_servers_config=mcp_servers
        )

        # Store with metadata for better tracking
        web_servers[resource_id] = {
            "instance": server_instance,
            "status": "created",
            "port": port,
            "host": host,
            "mcp_servers_count": len(mcp_servers),
            "created_at": asyncio.get_event_loop().time(),
        }

        logger.info(f"Created web server resource {resource_id} on {host}:{port}")
        return TextContent(type="text", text=resource_id)

    except Exception as e:
        error_msg = f"Failed to create web resource: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


@core_services.tool()
async def start_web_server(context: Context, resource_id: str) -> TextContent:
    """Starts a web server resource by its ID in a background task."""
    try:
        logger.debug(f"Starting web server: {resource_id}")

        # Validate resource_id
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError(
                f"Invalid resource_id: {resource_id}. Must be a non-empty string."
            )

        server_resource = web_servers.get(resource_id)
        if not server_resource:
            error_msg = f"Web server resource with ID '{resource_id}' not found."
            logger.error(error_msg)
            raise ValueError(error_msg)

        server_instance = server_resource["instance"]
        current_status = server_resource["status"]

        if current_status == "starting":
            logger.warning(f"Server {resource_id} is already starting")
            return TextContent(
                type="text", text=f"Web server {resource_id} is already starting."
            )

        if current_status == "running":
            logger.warning(f"Server {resource_id} is already running")
            return TextContent(
                type="text", text=f"Web server {resource_id} is already running."
            )

        # Update status immediately
        web_servers[resource_id]["status"] = "starting"
        web_servers[resource_id]["started_at"] = asyncio.get_event_loop().time()

        # Start the web server in a background task to avoid blocking the MCP response
        async def start_server_task():
            try:
                await server_instance.start()
                web_servers[resource_id]["status"] = "running"
                logger.info(
                    f"Web server {resource_id} started successfully on {server_resource['host']}:{server_resource['port']}"
                )
            except Exception as e:
                web_servers[resource_id]["status"] = "failed"
                web_servers[resource_id]["error"] = str(e)
                logger.error(f"Failed to start server {resource_id}: {e}")

        asyncio.create_task(start_server_task())

        # Give it a moment to start up, then return immediately
        await asyncio.sleep(0.2)

        success_msg = f"Web server {resource_id} startup initiated on {server_resource['host']}:{server_resource['port']}."
        return TextContent(type="text", text=success_msg)

    except Exception as e:
        error_msg = f"Failed to start web server: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


@core_services.tool()
async def destroy_web_resource(context: Context, resource_id: str) -> TextContent:
    """Stops and removes a web server resource by its ID."""
    try:
        logger.debug(f"Destroying web resource: {resource_id}")

        # Validate resource_id
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError(
                f"Invalid resource_id: {resource_id}. Must be a non-empty string."
            )

        server_resource = web_servers.pop(resource_id, None)
        if not server_resource:
            raise ValueError(f"Web server resource with ID '{resource_id}' not found.")

        server_instance = server_resource["instance"]

        try:
            await server_instance.cleanup()
            logger.debug(f"Server {resource_id} cleanup completed")
        except Exception as e:
            logger.error(f"Error during server {resource_id} cleanup: {e}")
            # Continue with destruction even if cleanup fails

        logger.info(f"Destroyed web server resource {resource_id}")
        return TextContent(type="text", text=f"Web server {resource_id} destroyed.")

    except Exception as e:
        error_msg = f"Failed to destroy web resource: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


@core_services.tool()
async def setup_web_app(
    context: Context, web_app_file: str = "", enable_local_tools: bool = False
) -> TextContent:
    """
    Sets up the web application environment by creating and starting a WebServer resource.
    This is a high-level orchestration tool that combines multiple steps into one operation.
    """
    resource_id = None
    try:
        logger.info(f"Setting up web application: {web_app_file or 'default'}")

        # Validate inputs
        if web_app_file and not isinstance(web_app_file, str):
            raise ValueError(
                f"Invalid web_app_file: {type(web_app_file)}. Must be a string."
            )

        if not isinstance(enable_local_tools, bool):
            raise ValueError(
                f"Invalid enable_local_tools: {type(enable_local_tools)}. Must be a boolean."
            )

        # Load configuration
        logger.debug("Loading configuration...")
        config = Config(web_app_file=web_app_file if web_app_file else None)
        logger.debug(f"Config loaded - port={config.port}, host={config.host}")

        # Configure MCP servers
        logger.debug("Configuring MCP servers...")
        mcp_servers = []

        # Add local tools if requested
        if enable_local_tools:
            logger.debug("Adding local_tools MCP server")
            local_tools_config = {"type": "stdio", "module": "src.server.local_tools"}
            mcp_servers.append(local_tools_config)

        # Parse web app file for additional MCP servers
        if web_app_file and os.path.exists(web_app_file):
            logger.debug(f"Parsing web app file: {web_app_file}")
            try:
                with open(web_app_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Extract YAML front matter
                import re

                yaml_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if yaml_match:
                    try:
                        import yaml

                        metadata = yaml.safe_load(yaml_match.group(1))
                        if metadata and "mcp_servers" in metadata:
                            additional_servers = metadata["mcp_servers"]
                            if isinstance(additional_servers, list):
                                logger.debug(
                                    f"Found {len(additional_servers)} additional MCP servers in web app file"
                                )
                                # Validate each server config
                                for i, server_config in enumerate(additional_servers):
                                    if not isinstance(server_config, dict):
                                        logger.warning(
                                            f"MCP server config {i} is not a dict, skipping: {server_config}"
                                        )
                                        continue
                                    if "type" not in server_config:
                                        logger.warning(
                                            f"MCP server config {i} missing 'type' field, skipping: {server_config}"
                                        )
                                        continue

                                    # Resolve template variables
                                    web_app_dir = os.path.dirname(
                                        os.path.abspath(web_app_file)
                                    )
                                    if "cwd" in server_config:
                                        original_cwd = server_config["cwd"]
                                        server_config["cwd"] = server_config[
                                            "cwd"
                                        ].replace("{{WEB_APP_DIR}}", web_app_dir)
                                        if original_cwd != server_config["cwd"]:
                                            logger.debug(
                                                f"Resolved template: {original_cwd} -> {server_config['cwd']}"
                                            )

                                    mcp_servers.append(server_config)
                            else:
                                logger.warning(
                                    "mcp_servers in YAML front matter is not a list, skipping"
                                )
                        else:
                            logger.debug("No MCP servers found in web app file YAML")
                    except yaml.YAMLError as e:
                        logger.warning(f"Failed to parse YAML front matter: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error parsing YAML: {e}")
                else:
                    logger.debug("No YAML front matter found in web app file")
            except FileNotFoundError:
                logger.warning(f"Web app file not found: {web_app_file}")
            except PermissionError:
                logger.warning(
                    f"Permission denied reading web app file: {web_app_file}"
                )
            except Exception as e:
                logger.error(f"Unexpected error reading web app file: {e}")
        else:
            logger.debug("No web app file provided or file does not exist")

        logger.debug(f"Total MCP servers configured: {len(mcp_servers)}")

        # Create and start web resource
        logger.debug("Creating web resource...")
        resource_id_content = await create_web_resource(
            context, port=config.port, host=config.host, mcp_servers=mcp_servers
        )
        resource_id = resource_id_content.text

        logger.debug("Starting web server...")
        result = await start_web_server(context, resource_id)

        success_msg = f"Web application setup complete. {result.text}"
        logger.info(success_msg)
        return TextContent(type="text", text=success_msg)

    except Exception as e:
        error_msg = f"Failed to setup web app: {str(e)}"
        logger.error(error_msg)

        # Cleanup on failure
        if resource_id:
            try:
                logger.debug(f"Attempting to destroy failed resource {resource_id}")
                await destroy_web_resource(context, resource_id)
                logger.debug("Failed resource destroyed successfully")
            except Exception as cleanup_error:
                logger.error(
                    f"Failed to destroy resource {resource_id}: {cleanup_error}"
                )

        raise RuntimeError(error_msg)


@core_services.tool()
async def list_web_resources(context: Context) -> TextContent:
    """Lists all web server resources and their current status."""
    try:
        logger.debug("Listing all web server resources")

        if not web_servers:
            return TextContent(type="text", text="No web server resources found.")

        resource_list = []
        for resource_id, resource_data in web_servers.items():
            status_info = {
                "id": resource_id,
                "status": resource_data["status"],
                "host": resource_data["host"],
                "port": resource_data["port"],
                "mcp_servers": resource_data["mcp_servers_count"],
                "created": f"{resource_data['created_at']:.2f}s ago",
            }

            if "started_at" in resource_data:
                status_info["started"] = f"{resource_data['started_at']:.2f}s ago"

            if "error" in resource_data:
                status_info["error"] = resource_data["error"]

            resource_list.append(status_info)

        result = f"Found {len(web_servers)} web server resource(s):\n"
        for resource in resource_list:
            result += f"  - {resource['id']}: {resource['status']} on {resource['host']}:{resource['port']}\n"
            result += f"    MCP servers: {resource['mcp_servers']}, Created: {resource['created']}\n"
            if "started" in resource:
                result += f"    Started: {resource['started']}\n"
            if "error" in resource:
                result += f"    Error: {resource['error']}\n"

        logger.debug(f"Listed {len(web_servers)} resources")
        return TextContent(type="text", text=result)

    except Exception as e:
        error_msg = f"Failed to list web resources: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


@core_services.tool()
async def get_web_resource_status(context: Context, resource_id: str) -> TextContent:
    """Gets detailed status information for a specific web server resource."""
    try:
        logger.debug(f"Getting status for resource: {resource_id}")

        # Validate resource_id
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError(
                f"Invalid resource_id: {resource_id}. Must be a non-empty string."
            )

        server_resource = web_servers.get(resource_id)
        if not server_resource:
            raise ValueError(f"Web server resource with ID '{resource_id}' not found.")

        current_time = asyncio.get_event_loop().time()
        status_info = {
            "resource_id": resource_id,
            "status": server_resource["status"],
            "host": server_resource["host"],
            "port": server_resource["port"],
            "mcp_servers_count": server_resource["mcp_servers_count"],
            "created_at": server_resource["created_at"],
            "uptime": f"{current_time - server_resource['created_at']:.2f}s",
        }

        if "started_at" in server_resource:
            status_info["started_at"] = server_resource["started_at"]
            status_info["running_time"] = (
                f"{current_time - server_resource['started_at']:.2f}s"
            )

        if "error" in server_resource:
            status_info["error"] = server_resource["error"]

        result = f"Status for web server {resource_id}:\n"
        for key, value in status_info.items():
            result += f"  {key}: {value}\n"

        logger.debug(f"Retrieved status for {resource_id}")
        return TextContent(type="text", text=result)

    except Exception as e:
        error_msg = f"Failed to get web resource status: {str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


async def main():
    """Main function to run the MCP server with stdio transport."""
    log_level = os.environ.get("CORE_SERVICES_LOG_LEVEL", "INFO")
    configure_subprocess_logging(log_level)

    logger.info("Starting core services MCP server")

    try:
        # The tool implementations are registered with `core_services`
        # Now, we just need to run the server.
        logger.debug("Core services initialized, starting stdio server")
        await core_services.run_stdio_async()
    except Exception as e:
        logger.error(f"Core services failed to run: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
