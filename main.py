import argparse
import asyncio
import json
import os
import sys

from dotenv import dotenv_values
from aiohttp import web, ClientSession

from src.server import (
    create_app,
    get_loggers,
    run_local_tools_stdio_server as run_local_tools,
    _parse_webapp_file,
    DEFAULT_WEB_APP_FILE,
)


def initialize_configuration():
    """
    Parses command-line arguments, resolves configuration with environment variables and defaults,
    loads web app prompt file, constructs the system prompt, and initializes the OpenAI client.
    Returns a dictionary containing all resolved configurations and the client.
    Exits if critical configurations (like API key) are missing.
    """
    app_logger, _, __ = get_loggers()

    DEFAULT_PORT = 8080
    DEFAULT_OPENAI_MODEL_NAME = "gpt-4o"
    DEFAULT_OPENAI_TEMPERATURE = 0.7
    DEFAULT_MAX_TURNS = 25
    DEFAULT_CONTEXT_WINDOW_MAX = 0  # Disabled by default

    # Pre-parser to find --env-file without triggering help or errors for other args
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", type=str, default=None)
    args_partial, _ = pre_parser.parse_known_args()

    # Load .env file if specified, otherwise try default .env in current directory
    env_vars = None
    env_file_to_load = args_partial.env_file
    if not env_file_to_load:
        default_env_path = os.path.join(os.getcwd(), ".env")
        if os.path.isfile(default_env_path):
            env_file_to_load = default_env_path

    if env_file_to_load:
        try:
            env_vars = dotenv_values(env_file_to_load)
            if env_vars:
                app_logger.info(
                    f"Loaded environment variables from .env file: {env_file_to_load}"
                )
                app_logger.info(f".env keys loaded: {sorted(env_vars.keys())}")
            else:
                app_logger.warning(
                    f".env file specified but empty or not found: {env_file_to_load}"
                )
        except Exception as e:
            app_logger.error(f"Error loading .env file '{env_file_to_load}': {e}")
            env_vars = None

    def get_env(key, default=None):
        if env_vars is not None and key in env_vars and env_vars[key] is not None:
            return env_vars[key]
        return os.environ.get(key, default)

    # Now, define the full parser with all arguments, which will handle --help correctly
    parser = argparse.ArgumentParser(description="LLM HTTP Server")
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to a .env file to load environment variables from. Defaults to '.env' in the current directory if it exists.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,  # We will resolve the default after parsing
        help=f"Port to run the server on (default: {DEFAULT_PORT}, or from PORT env var)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=get_env("OPENAI_API_KEY"),
        help="OpenAI API Key (can also be set with OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=get_env("OPENAI_BASE_URL"),
        help="Optional OpenAI compatible base URL (can also be set with OPENAI_BASE_URL env var)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,  # Resolve default after parsing
        help=f"OpenAI Model Name (default: {DEFAULT_OPENAI_MODEL_NAME}, or from OPENAI_MODEL_NAME env var)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,  # Resolve default after parsing
        help=f"OpenAI Temperature (default: {DEFAULT_OPENAI_TEMPERATURE}, or from OPENAI_TEMPERATURE env var)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,  # We will resolve the default after parsing
        help=f"Maximum number of turns for the agent (default: {DEFAULT_MAX_TURNS}, or from MAX_TURNS env var)",
    )
    parser.add_argument(
        "--context-window-max",
        type=int,
        default=None,
        help=f"Maximum token count for conversation history before summarizing. 0 to disable. (default: {DEFAULT_CONTEXT_WINDOW_MAX}, or from CONTEXT_WINDOW_MAX env var)",
    )
    parser.add_argument(
        "--web-app-file",
        type=str,
        default=get_env("WEB_APP_FILE"),
        help="Path to a markdown file with YAML front matter containing web application instructions and optional MCP server configuration (can also be set with WEB_APP_FILE env var)",
    )
    parser.add_argument(
        "--save-conversations",
        action="store_true",
        default=str(get_env("SAVE_CONVERSATIONS", "")).lower() in ("true", "1", "yes"),
        help="Save conversation history to files (can also be set with SAVE_CONVERSATIONS env var)",
    )
    parser.add_argument(
        "--local-tools",
        action=argparse.BooleanOptionalAction,
        default=str(get_env("LOCAL_TOOLS_ENABLED", "true")).lower()
        in ("true", "1", "yes"),
        help="Enable or disable the in-process local tools server.",
    )

    # One-shot mode for round-trip evaluation
    parser.add_argument(
        "--one-shot",
        action="store_true",
        default=False,
        help=(
            "Start the server, issue a single GET / request internally, print the raw HTTP response, "
            "and then shut the server down. Useful for automated round-trip evaluation."
        ),
    )
    parser.add_argument(
        "--local-tools-stdio",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,  # Hidden from help
    )

    # Now parse all arguments for real
    args = parser.parse_args()

    # --- Now build the final config from args and get_env ---
    config = {}
    config["PORT"] = (
        args.port if args.port is not None else int(get_env("PORT", DEFAULT_PORT))
    )
    config["API_KEY"] = args.api_key  # Already resolved from get_env in default
    config["OPENAI_BASE_URL"] = args.base_url  # Already resolved
    config["WEB_APP_FILE"] = args.web_app_file  # Already resolved
    config["SAVE_CONVERSATIONS"] = args.save_conversations  # Already resolved
    config["MAX_TURNS"] = (
        args.max_turns
        if args.max_turns is not None
        else int(get_env("MAX_TURNS", DEFAULT_MAX_TURNS))
    )
    config["CONTEXT_WINDOW_MAX"] = (
        args.context_window_max
        if args.context_window_max is not None
        else int(get_env("CONTEXT_WINDOW_MAX", DEFAULT_CONTEXT_WINDOW_MAX))
    )
    config["LOCAL_TOOLS_ENABLED"] = args.local_tools

    # Store one-shot flag
    config["ONE_SHOT"] = args.one_shot
    config["LOCAL_TOOLS_STDIO"] = args.local_tools_stdio

    if not config["API_KEY"]:
        app_logger.error(
            "OpenAI API Key not provided. Please set OPENAI_API_KEY environment variable or use --api-key."
        )
        exit(1)

    llm_http_server_prompt_base = ""
    try:
        with open("src/prompts/system.md", "r", encoding="utf-8") as f:
            llm_http_server_prompt_base = f.read()
        app_logger.info("Loaded system prompt from src/prompts/system.md")
    except FileNotFoundError:
        app_logger.error(
            "System prompt file not found at src/prompts/system.md. Exiting."
        )
        exit(1)
    except Exception as e:
        app_logger.exception(f"Error reading system prompt file: {e}")
        exit(1)

    error_llm_system_prompt_template = ""
    try:
        with open("src/prompts/error.md", "r", encoding="utf-8") as f:
            error_llm_system_prompt_template = f.read()
        app_logger.info("Loaded error prompt from src/prompts/error.md")
    except FileNotFoundError:
        app_logger.error(
            "Error prompt file not found at src/prompts/error.md. Exiting."
        )
        exit(1)
    except Exception as e:
        app_logger.exception(f"Error reading error prompt file: {e}")
        exit(1)

    config["ERROR_LLM_SYSTEM_PROMPT_TEMPLATE"] = error_llm_system_prompt_template

    web_app_prompt_content_from_file = ""
    webapp_yaml_data = {}

    # Determine which web app file to use
    web_app_file_to_use = config["WEB_APP_FILE"] or DEFAULT_WEB_APP_FILE
    is_using_default = not config["WEB_APP_FILE"]

    try:
        webapp_yaml_data, web_app_prompt_content_from_file = _parse_webapp_file(
            web_app_file_to_use
        )

        if web_app_prompt_content_from_file.strip():
            if is_using_default:
                app_logger.info(
                    f"No WEB_APP_FILE specified. Using default web app: {web_app_file_to_use}"
                )
            else:
                app_logger.info(
                    f"Successfully loaded web app content from: {web_app_file_to_use}"
                )

            if webapp_yaml_data:
                app_logger.info(
                    f"Loaded webapp metadata: {list(webapp_yaml_data.keys())}"
                )
        else:
            app_logger.warning(
                f"Web app file '{web_app_file_to_use}' has no content. Using empty content."
            )
            web_app_prompt_content_from_file = ""

    except FileNotFoundError:
        app_logger.warning(
            f"Web app file not found: {web_app_file_to_use}. Using empty content."
        )
    except Exception:
        app_logger.exception(f"Error reading web app file '{web_app_file_to_use}':")
        app_logger.warning("Proceeding with empty content due to error.")

    # Extract MCP servers from webapp file if present
    if webapp_yaml_data.get("mcp_servers"):
        # Resolve {{WEB_APP_DIR}} sentinel in MCP server arguments
        web_app_dir = os.path.dirname(os.path.abspath(web_app_file_to_use))
        mcp_servers_data = webapp_yaml_data["mcp_servers"]

        # Expect list format for mcp_servers
        if not isinstance(mcp_servers_data, list):
            app_logger.error(
                f"MCP servers configuration must be a list, got {type(mcp_servers_data).__name__}. "
                "Please update your webapp file to use list format."
            )
            exit(1)

        for server_config in mcp_servers_data:
            if "args" in server_config and isinstance(server_config["args"], list):
                server_config["args"] = [
                    str(arg).replace("{{WEB_APP_DIR}}", web_app_dir)
                    for arg in server_config["args"]
                ]
            if server_config.get("cwd"):
                server_config["cwd"] = str(server_config["cwd"]).replace(
                    "{{WEB_APP_DIR}}", web_app_dir
                )
            if "env" in server_config and isinstance(server_config["env"], dict):
                for key, value in server_config["env"].items():
                    server_config["env"][key] = str(value).replace(
                        "{{WEB_APP_DIR}}", web_app_dir
                    )

        config["MCP_SERVERS"] = json.dumps(mcp_servers_data)
        app_logger.info(
            f"Loaded {len(mcp_servers_data)} MCP server(s) from webapp file"
        )
    else:
        config["MCP_SERVERS"] = "[]"  # Start with an empty list string
        app_logger.info("No MCP servers configured in webapp file")

    app_logger.info(f"Local tools enabled: {config['LOCAL_TOOLS_ENABLED']}")

    # Store webapp metadata for later use
    config["WEBAPP_METADATA"] = webapp_yaml_data

    # Prepare dynamic examples for the LLM server prompt
    jinja_ready_llm_server_prompt = llm_http_server_prompt_base

    if web_app_prompt_content_from_file.strip():
        web_app_rules_section_content = web_app_prompt_content_from_file.strip()
        # Wrap user-provided content in {% raw %} to prevent it from being templated by Jinja2
        system_prompt_template = (
            f"{jinja_ready_llm_server_prompt.strip()}\n\n"
            "<web_application_rules>\n"
            "{% raw %}\n"
            f"{web_app_rules_section_content}\n"
            "{% endraw %}\n"
            "</web_application_rules>"
        )
    else:
        # If no content was loaded (error case), just use the base prompt
        system_prompt_template = jinja_ready_llm_server_prompt.strip()

    config["SYSTEM_PROMPT_TEMPLATE"] = system_prompt_template
    config["WEB_APP_RULES"] = web_app_prompt_content_from_file

    model_name = (
        args.model
        if args.model is not None
        else get_env("OPENAI_MODEL_NAME", DEFAULT_OPENAI_MODEL_NAME)
    )

    config["OPENAI_MODEL_NAME"] = model_name
    config["OPENAI_TEMPERATURE"] = (
        args.temperature
        if args.temperature is not None
        else float(get_env("OPENAI_TEMPERATURE", DEFAULT_OPENAI_TEMPERATURE))
    )

    return config


async def _perform_one_shot(app: web.Application, host: str, port: int):
    """Run the server long enough to perform a single internal GET / request.

    This utility is intended for automated round-trip evaluation. It starts the
    aiohttp server, makes a single HTTP request to the root path, prints the
    complete raw HTTP response to stdout, and then shuts the server down
    cleanly.
    """
    app_logger, _, __ = get_loggers()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    # Brief pause to ensure the server is fully ready before sending the request
    await asyncio.sleep(0.1)

    # Determine the actual port in case 0 was specified (ephemeral port)
    actual_port = port
    try:
        if actual_port == 0:
            # Access the first socket bound by TCPSite
            for _site in runner.sites:
                if (
                    isinstance(_site, web.TCPSite)
                    and _site._server
                    and _site._server.sockets
                ):
                    actual_port = _site._server.sockets[0].getsockname()[1]
                    break
    except Exception:
        # Fallback to provided port if introspection fails
        pass

    try:
        async with ClientSession() as session:
            async with session.get(f"http://{host}:{actual_port}/") as resp:
                version = f"{resp.version.major}.{resp.version.minor}"
                status_line = f"HTTP/{version} {resp.status} {resp.reason}"
                header_lines = [f"{k}: {v}" for k, v in resp.headers.items()]
                body = await resp.text()

                raw_response = (
                    status_line + "\r\n" + "\r\n".join(header_lines) + "\r\n\r\n" + body
                )

                # Log the raw HTTP response for evaluation tooling using the standard logger
                app_logger.info("One-shot raw HTTP response:\n%s", raw_response)
    finally:
        # Gracefully shut the server down (runs on_shutdown hooks)
        await runner.cleanup()


def run_server():
    """Initializes configuration, sets up the aiohttp app, and starts the HTTP server."""
    config = initialize_configuration()
    app_logger, access_logger, _ = get_loggers()
    app = create_app(config)

    port_to_use = config["PORT"]
    app_logger.info(
        f"LLM HTTP Server (Async using aiohttp) starting on port {port_to_use}"
    )
    app_logger.info(
        f"Configuration: API Key: {'Set' if config['API_KEY'] else 'NOT SET (REQUIRED!)'}, "
        f"Base URL: {config['OPENAI_BASE_URL'] or 'Default'}, Model: {config['OPENAI_MODEL_NAME']}, Temp: {config['OPENAI_TEMPERATURE']}, "
        f"Max Turns: {config['MAX_TURNS']}, Context Window Max: {config['CONTEXT_WINDOW_MAX']}, "
        f"Local Tools: {'Enabled' if config['LOCAL_TOOLS_ENABLED'] else 'Disabled'}, "
        f"Web App File: {config.get('WEB_APP_FILE') or 'Default (examples/default_info_site/prompt.md)'}, Save Conversations: {config['SAVE_CONVERSATIONS']}"
    )
    app_logger.info(
        f"To override, use command-line arguments (e.g., --port {port_to_use}) or environment variables."
    )
    app_logger.info(f"Access the server at http://localhost:{port_to_use}")

    # One-shot evaluation mode
    if config.get("ONE_SHOT"):
        app_logger.info(
            "Running in one-shot evaluation mode – the server will handle a single internal request and then exit."
        )
        asyncio.run(_perform_one_shot(app, "127.0.0.1", port_to_use))
        return

    # Normal long-running server
    web.run_app(app, host="0.0.0.0", port=port_to_use, access_log=access_logger)


if __name__ == "__main__":
    # This mechanism decides which server to run based on command-line arguments.
    # The main web server can spawn this script with '--local-tools-stdio'
    # to create the separate tools process.
    if "--local-tools-stdio" in sys.argv:
        run_local_tools()
    else:
        run_server()
