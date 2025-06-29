import asyncio
import logging
import jinja2
import yaml
import re
import uuid

from mcp.server.fastmcp.server import FastMCP as Server, Context
from mcp.types import Tool, TextContent
from src.config import Config
from src.server.web_resource import WebServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory store for our web server resources
web_servers: dict[str, WebServer] = {}

# Load config once when the server starts
config = Config()

server = Server(
    "core-services",
    title="Core Services",
    description="Provides core services for the application.",
)


@server.tool()
async def create_web_resource(context: Context, port: int, host: str = "0.0.0.0") -> TextContent:
    """Creates a new web server resource and returns its unique ID."""
    resource_id = str(uuid.uuid4())
    server_instance = WebServer(port=port, host=host)
    web_servers[resource_id] = server_instance
    logger.info(f"Created web server resource with ID: {resource_id}")
    return TextContent(type="text", text=resource_id)


@server.tool()
async def start_web_server(context: Context, resource_id: str) -> TextContent:
    """Starts a web server resource by its ID."""
    server_instance = web_servers.get(resource_id)
    if not server_instance:
        raise ValueError(f"Web server resource with ID '{resource_id}' not found.")
    
    await server_instance.start()
    return TextContent(type="text", text=f"Web server {resource_id} started.")


@server.tool()
async def destroy_web_resource(context: Context, resource_id: str) -> TextContent:
    """Stops and removes a web server resource by its ID."""
    server_instance = web_servers.pop(resource_id, None)
    if not server_instance:
        raise ValueError(f"Web server resource with ID '{resource_id}' not found.")
    
    await server_instance.stop()
    logger.info(f"Destroyed web server resource with ID: {resource_id}")
    return TextContent(type="text", text=f"Web server {resource_id} destroyed.")


@server.tool()
async def update_web_resource_config(context: Context, resource_id: str) -> TextContent:
    """Updates the configuration of a web server resource."""
    server_instance = web_servers.get(resource_id)
    if not server_instance:
        raise ValueError(f"Web server resource with ID '{resource_id}' not found.")

    # For now, we'll just add a default route to simulate a config update.
    # We will import the request handler from app.py
    from src.app import handle_http_request
    server_instance.add_route("/{path:.*}", handle_http_request)
    
    return TextContent(type="text", text=f"Web server {resource_id} configuration updated.")


@server.tool()
async def connect_mcp_server(context: Context, resource_id: str, mcp_server_id: str) -> TextContent:
    """Connects a web server resource to another MCP server."""
    # This is a placeholder for now. In the future, this would involve
    # setting up the connection between the web server and the MCP server.
    logger.info(f"Simulating connection of web server {resource_id} to MCP server {mcp_server_id}")
    return TextContent(type="text", text=f"Web server {resource_id} connected to {mcp_server_id}.")


@server.tool()
async def get_config(context: Context) -> TextContent:
    """Returns the current application configuration as a JSON string."""
    return TextContent(type="text", text=config.model_dump_json(indent=2))


@server.tool()
async def render_template(context: Context, template_str: str, template_context: dict) -> TextContent:
    """Renders a Jinja2 template with the given context."""
    try:
        template = jinja2.Template(template_str)
        rendered_content = template.render(template_context)
        return TextContent(type="text", text=rendered_content)
    except jinja2.exceptions.TemplateSyntaxError as e:
        raise ValueError(f"Jinja2 template syntax error: {e}")
    except Exception as e:
        raise ValueError(f"Error rendering template: {e}")


@server.tool()
async def parse_webapp_file(context: Context, path: str) -> TextContent:
    """
    Parses a markdown file with YAML front matter.
    Returns a JSON object with 'metadata' and 'content' keys.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            file_content = f.read()

        # Regex to find YAML front matter
        match = re.match(r"^---\s*\n(.*?\n)---\s*\n(.*)", file_content, re.DOTALL)

        if match:
            yaml_str = match.group(1)
            content_str = match.group(2)
            metadata = yaml.safe_load(yaml_str)
        else:
            metadata = {}
            content_str = file_content

        result = {
            "metadata": metadata,
            "content": content_str,
        }
        import json
        return TextContent(type="text", text=json.dumps(result, indent=2))

    except FileNotFoundError:
        raise
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML front matter: {e}")
    except Exception as e:
        raise ValueError(f"Error parsing web app file: {e}")


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