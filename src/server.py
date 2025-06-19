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

import abc
import asyncio
import http.cookies
import http.server
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import formatdate

import jinja2
import yaml
from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from agents.items import MessageOutputItem
from agents.mcp import (
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.model_settings import ModelSettings
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from aiohttp import web
from mcp.server.fastmcp.server import Context
from mcp.server.fastmcp.server import FastMCP as Server
from mcp.types import CallToolResult, TextContent
from pythonjsonlogger import jsonlogger
from rich.logging import RichHandler

# Default web app file path
DEFAULT_WEB_APP_FILE = "examples/default_info_site/prompt.md"


def get_loggers():
    return (
        logging.getLogger("llm_http_server_app"),
        logging.getLogger("http_access"),
        logging.getLogger("conversation_history"),
    )


class AbstractSessionStore(abc.ABC):
    """Abstract base class for session storage."""

    @abc.abstractmethod
    async def get_history(self, session_id: str) -> list[dict]:
        """Retrieve the conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def record_turn(self, session_id: str, role: str, text_content: str) -> None:
        """Record a new turn in the conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def replace_history(self, session_id: str, history: list[dict]) -> None:
        """Replace the entire conversation history for a given session ID."""
        pass

    @abc.abstractmethod
    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        pass

    @abc.abstractmethod
    async def update_token_count(self, session_id: str, count: int) -> None:
        """Update the token count for the session's history."""
        pass

    @abc.abstractmethod
    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories, typically on server shutdown."""
        pass


# --- Local Tools Server Definition (from former local_tools_server.py) ---

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
async def get_conversation_history(context: Context, session_id: str) -> CallToolResult:
    """
    Retrieves the full, ordered conversation history for a given session ID.
    """
    session_store: AbstractSessionStore = local_mcp_server.session_store
    history = await session_store.get_history(session_id)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(history, indent=2))]
    )


@local_mcp_server.tool()
async def update_session_history(
    context: Context, session_id: str, new_history_json: str
) -> CallToolResult:
    """
    Replaces the entire conversation history for a session with a new one.
    """
    session_store: AbstractSessionStore = local_mcp_server.session_store
    try:
        new_history = json.loads(new_history_json)
        if not isinstance(new_history, list):
            raise TypeError("JSON must decode to a list of message objects.")
        await session_store.replace_history(session_id, new_history)
        return CallToolResult(
            content=[
                TextContent(
                    type="text", text="Conversation history replaced successfully."
                )
            ]
        )
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"Invalid history format: {e}") from e


def create_local_tools_stdio_server(
    global_state: dict, session_store: AbstractSessionStore
) -> Server:
    """
    Creates the StdioServer application for the local tools.
    """
    local_mcp_server.global_state = global_state
    local_mcp_server.session_store = session_store

    @asynccontextmanager
    async def lifespan(server_instance: Server) -> AsyncIterator[dict]:
        """Manage server startup and shutdown, yielding state to the context."""
        # State is attached directly to the server instance for stdio transport.
        yield

    local_mcp_server.lifespan = lifespan

    tools_logger = logging.getLogger("local_tools_server")
    tools_logger.setLevel(logging.INFO)
    if not tools_logger.handlers:
        handler = RichHandler(show_path=False)
        tools_logger.addHandler(handler)
        tools_logger.propagate = False

    return local_mcp_server


# --- Main Application ---
class InMemorySessionStore(AbstractSessionStore):
    """In-memory implementation of the session store."""

    def __init__(self, save_to_disk: bool = True):
        self._histories: dict[str, list[dict]] = {}
        self._token_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._save_to_disk = save_to_disk
        # Get the logger used elsewhere in the module for consistency
        self._logger = logging.getLogger("llm_http_server_app")  # Using app_logger
        self._conversation_logger = logging.getLogger(
            "conversation_history"
        )  # Using conversation_logger

    async def get_history(self, session_id: str) -> list[dict]:
        """Retrieve the conversation history for a given session ID."""
        async with self._lock:
            return self._histories.get(session_id, [])

    async def record_turn(self, session_id: str, role: str, text_content: str) -> None:
        """Record a new turn in the conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to record conversation turn without a session_id in InMemorySessionStore. Turn not stored."
            )
            return
        async with self._lock:
            history = self._histories.setdefault(session_id, [])
            entry = {
                "role": role,
                "content": text_content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            history.append(entry)
            self._conversation_logger.info(  # Changed to use self._conversation_logger
                f"Recorded '{role}' turn for session_id '{session_id}' via InMemorySessionStore. Total turns: {len(history)}"
            )

    async def replace_history(self, session_id: str, history: list[dict]) -> None:
        """Replace the entire conversation history for a given session ID."""
        if not session_id:
            self._logger.warning(
                "Attempted to replace conversation history without a session_id in InMemorySessionStore. History not replaced."
            )
            return
        async with self._lock:
            self._histories[session_id] = history
            self._token_counts[session_id] = 0  # Reset token count
            self._logger.info(
                f"Replaced history for session '{session_id}'. New history has {len(history)} turn(s). Token count reset."
            )

    async def get_token_count(self, session_id: str) -> int:
        """Retrieve the token count for the last known state of the history."""
        async with self._lock:
            return self._token_counts.get(session_id, 0)

    async def update_token_count(self, session_id: str, count: int) -> None:
        """Update the token count for the session's history."""
        if not session_id:
            return
        async with self._lock:
            self._token_counts[session_id] = count
            self._logger.info(
                f"Updated token count for session '{session_id}' to {count}."
            )

    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories to files on server shutdown."""
        if not self._save_to_disk:
            self._logger.info(
                "Conversation saving to disk is disabled. Skipping save_all_sessions_on_shutdown (via InMemorySessionStore)."
            )
            return

        try:
            os.makedirs(log_directory, exist_ok=True)
            self._logger.info(
                f"Ensured conversation log directory exists: {log_directory} (via InMemorySessionStore)"
            )

            async with self._lock:  # Ensure exclusive access for saving
                if not self._histories:
                    self._logger.info(
                        "No conversation histories to save (via InMemorySessionStore)."
                    )
                    return

                saved_count = 0
                for session_id, history_list in self._histories.items():
                    if not history_list:
                        continue
                    file_path = os.path.join(log_directory, f"{session_id}.json")
                    try:
                        # Using standard open for simplicity, can be aiofiles if becomes bottleneck
                        with open(file_path, "w") as f:
                            json.dump(history_list, f, indent=2)
                        self._logger.info(
                            f"Conversation history for session '{session_id}' saved to {file_path} (via InMemorySessionStore)"
                        )
                        saved_count += 1
                    except Exception as e:
                        self._logger.exception(
                            f"Failed to save conversation history for session '{session_id}' to {file_path} (via InMemorySessionStore): {e}"
                        )
                if saved_count > 0:
                    self._logger.info(
                        f"Successfully saved {saved_count} session histories (via InMemorySessionStore)."
                    )
                else:
                    self._logger.info(
                        "No non-empty session histories were saved (via InMemorySessionStore)."
                    )
        except Exception as e:
            self._logger.exception(
                f"Failed to create/access conversation log directory '{log_directory}' in InMemorySessionStore: {e}"
            )


# --- Static Prompts and Global State ---
LLM_HTTP_SERVER_PROMPT_BASE = ""  # This will be loaded from a file


ERROR_PAGE_TEMPLATE_STR = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Server Error: {{ message }}</title>
    <style>
        body {font-family: sans-serif; margin:20px;}
        h1 {color: #cc0000;}
        .details {background-color: #f0f0f0; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word;}
    </style>
</head>
<body>
    <h1>HTTP {{ status_code }} - {{ message }}</h1>
    <p class="details">{{ error_details | e }}</p>
</body>
</html>
"""
ERROR_PAGE_TEMPLATE = jinja2.Template(ERROR_PAGE_TEMPLATE_STR)


# This is loaded by main.py and passed in the config
# ERROR_LLM_SYSTEM_PROMPT_TEMPLATE = ""


# --- In-memory Conversation History Storage (for logging and potential rehydration) ---
# Key: session_id, Value: list of conversation turns (OpenAI format: {"role": "user/assistant", "content": "..."})


# --- Logging Configuration (Global for simplicity, initialized early) ---
# Custom formatter for JSON logs
class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            log_record["timestamp"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        if log_record.get("levelname"):
            log_record["severity"] = log_record["levelname"].upper()
            del log_record["levelname"]  # Remove original levelname
        else:
            log_record["severity"] = "INFO"  # Default severity
        if not log_record.get("logger"):
            log_record["logger"] = record.name


app_logger, access_logger, conversation_logger = get_loggers()
app_logger.setLevel(logging.INFO)
access_logger.setLevel(logging.INFO)
conversation_logger.setLevel(logging.INFO)

# Use the custom JSON formatter
# The format string for JsonFormatter defines which record attributes to pick for the log output.
# We can add more fields here if needed, e.g. '%(module)s %(funcName)s'
json_formatter = CustomJsonFormatter(
    "%(timestamp)s %(severity)s %(logger)s %(message)s"
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(json_formatter)  # Apply JSON formatter

# Clear existing handlers and add the new JSON one
for logger_instance in [app_logger, access_logger, conversation_logger]:
    if logger_instance.hasHandlers():
        logger_instance.handlers.clear()
    # Replace StreamHandler with RichHandler for colorful output
    rich_handler = RichHandler(
        rich_tracebacks=True, show_path=False
    )  # show_path=False to keep logs cleaner
    logger_instance.addHandler(rich_handler)
    logger_instance.propagate = False  # Prevent duplicate logs from root logger


# --- MCP Tool Call Logging ---
# This section is handled by McpAgentClient. No monkey-patching needed.
app_logger.info(
    "McpAgentClient handles tool call logging internally. No monkey-patching needed."
)


def _parse_webapp_file(file_path):
    """
    Parse a markdown file with YAML front matter.
    Returns a tuple of (yaml_data, markdown_content).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check if the file starts with YAML front matter
        if content.startswith("---\n"):
            # Find the end of the YAML front matter
            match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
            if match:
                yaml_content = match.group(1)
                markdown_content = match.group(2)

                # Parse the YAML
                yaml_data = yaml.safe_load(yaml_content) if yaml_content.strip() else {}

                return yaml_data, markdown_content.strip()
            else:
                # Invalid front matter format
                app_logger.warning(f"Invalid YAML front matter format in {file_path}")
                return {}, content
        else:
            # No front matter, treat as plain markdown/text
            return {}, content

    except yaml.YAMLError as e:
        app_logger.error(f"YAML parsing error in {file_path}: {e}")
        return {}, ""
    except Exception as e:
        app_logger.error(f"Error reading webapp file {file_path}: {e}")
        return {}, ""


async def _initialize_mcp_servers_and_agent(config, app: web.Application):
    """
    Initialize MCP servers based on configuration and create an agent with them.
    Returns the configured agent.
    """
    mcp_servers = []
    app["mcp_server_lifecycles"] = []

    # Initialize external MCP servers from webapp config
    if config.get("MCP_SERVERS"):
        try:
            mcp_configs = json.loads(config["MCP_SERVERS"])
            app_logger.info(
                f"Initializing {len(mcp_configs)} external MCP server(s) using agents.mcp"
            )

            for mcp_config in mcp_configs:
                server_type = mcp_config.get("type", "stdio").lower()
                server = None

                if server_type == "stdio":
                    params = {
                        "command": mcp_config["command"],
                        "args": mcp_config.get("args", []),
                        "cwd": mcp_config.get("cwd"),
                        "env": mcp_config.get("env"),
                    }
                    server = MCPServerStdio(params=params)
                elif server_type == "sse":
                    server = MCPServerSse(params={"url": mcp_config["url"]})
                elif server_type == "streamable_http":
                    server = MCPServerStreamableHttp(params={"url": mcp_config["url"]})
                else:
                    app_logger.error(f"Unsupported MCP server type: {server_type}")
                    continue

                await server.__aenter__()
                app["mcp_server_lifecycles"].append(server)
                tools = await server.list_tools()

                if tools:
                    app_logger.info(f"Added and connected to MCP server: {server.name}")
                    tool_names = sorted([t.name for t in tools])
                    app_logger.info(f"  └─ Discovered tools: {tool_names}")
                    mcp_servers.append(server)
                else:
                    app_logger.warning(
                        f"MCP server {server.name} did not provide any tools and will be ignored."
                    )
                    await server.__aexit__(None, None, None)
                    app["mcp_server_lifecycles"].pop()

        except json.JSONDecodeError as e:
            app_logger.error(f"Invalid JSON in MCP_SERVERS configuration: {e}")
        except Exception as e:
            app_logger.exception(f"Error initializing external MCP servers: {e}")

    # Spawn and connect to the local tools stdio server if enabled
    if config.get("LOCAL_TOOLS_ENABLED"):
        try:
            app_logger.info("Spawning local tools stdio server as a subprocess.")
            command_parts = [sys.executable, "main.py", "--local-tools-stdio"]
            params = {
                "command": command_parts[0],
                "args": command_parts[1:],
            }
            local_server_client = MCPServerStdio(params=params)

            await local_server_client.__aenter__()
            app["mcp_server_lifecycles"].append(local_server_client)
            tools = await local_server_client.list_tools()

            if tools:
                app_logger.info(
                    f"Successfully connected to local tools stdio server: {local_server_client.name}"
                )
                tool_names = sorted([t.name for t in tools])
                app_logger.info(f"  └─ Discovered tools: {tool_names}")
                mcp_servers.append(local_server_client)
            else:
                app_logger.warning(
                    f"Local tools stdio server {local_server_client.name} did not provide any tools and will be ignored."
                )
                await local_server_client.__aexit__(None, None, None)
                app["mcp_server_lifecycles"].pop()
        except Exception:
            app_logger.exception("Failed to spawn or connect to local tools server.")

    # Create model - either default or custom client
    if config.get("OPENAI_BASE_URL"):
        custom_client = AsyncOpenAI(
            api_key=config.get("API_KEY"),
            base_url=config.get("OPENAI_BASE_URL"),
        )
        model = OpenAIChatCompletionsModel(
            model=config["OPENAI_MODEL_NAME"], openai_client=custom_client
        )
        set_tracing_disabled(disabled=True)
        app_logger.info(
            f"Using custom OpenAI client with base_url: {config.get('OPENAI_BASE_URL')}"
        )
        app_logger.info("Tracing disabled for custom provider")
    else:
        model = config["OPENAI_MODEL_NAME"]

    agent = Agent(
        name="HTTP LLM Server Agent",
        instructions="You are an LLM powering an HTTP server. Use available tools to enhance your responses when appropriate.",
        model=model,
        model_settings=ModelSettings(
            temperature=config["OPENAI_TEMPERATURE"],
            include_usage=True,
        ),
        mcp_servers=mcp_servers,
    )

    app_logger.info(f"Agent initialized with {len(mcp_servers)} MCP server(s)")
    app["agent"] = agent
    return agent


# Ensure these helpers are defined before handle_http_request
async def _get_raw_request_aiohttp(request: web.Request) -> str:
    """
    Constructs the raw HTTP request string from an aiohttp.web.Request object.
    """
    raw_request_line_str = f"{request.method} {request.path_qs} HTTP/{request.version.major}.{request.version.minor}"
    header_lines = [f"{key}: {value}" for key, value in request.headers.items()]
    body_str = ""
    if request.can_read_body:
        body_bytes = await request.read()
        charset = request.charset or "utf-8"
        try:
            body_str = body_bytes.decode(charset)
        except (UnicodeDecodeError, LookupError):
            app_logger.warning(
                f"Could not decode request body with charset {charset}, used latin-1 fallback."
            )
            body_str = body_bytes.decode("latin-1", "replace")

    full_request_parts = [raw_request_line_str] + header_lines
    if body_str or request.can_read_body:
        full_request_parts.append("")
        full_request_parts.append(body_str)
    return "\r\n".join(full_request_parts)


async def _send_llm_error_response_aiohttp(
    request: web.Request, status_code: int, message: str, error_details: str = ""
) -> web.Response:
    """
    Sends an error response. Tries to generate a styled error page via LLM,
    but falls back to a static template if it fails.
    """
    ERROR_LLM_SYSTEM_PROMPT_TEMPLATE = request.app["error_llm_system_prompt_template"]

    async def _fallback_response():
        # This is the old, reliable static template method
        html_body = ERROR_PAGE_TEMPLATE.render(
            status_code=status_code,
            message=message,
            error_details=error_details,
        )
        return web.Response(
            text=html_body,
            status=status_code,
            content_type="text/html",
            charset="utf-8",
            headers={"Connection": "close"},
        )

    agent = request.app.get("agent")
    web_app_rules = request.app.get("web_app_rules")

    if not agent or not web_app_rules:
        app_logger.warning(
            "Agent or web_app_rules not available for LLM-generated error page. Falling back to static."
        )
        return await _fallback_response()

    try:
        app_logger.info(
            f"Attempting to generate a styled error page with LLM for status {status_code}..."
        )

        template = jinja2.Template(ERROR_LLM_SYSTEM_PROMPT_TEMPLATE)
        error_system_prompt = template.render(
            status_code=status_code,
            message=message,
            error_details=error_details,
            web_app_rules=web_app_rules,
        )

        messages = [
            {"role": "system", "content": error_system_prompt},
            {
                "role": "user",
                "content": f"Please generate the HTTP response for the {status_code} error page now.",
            },
        ]

        output_item = await Runner.run(agent, messages)

        if not isinstance(output_item, MessageOutputItem) or not output_item.content:
            app_logger.error(
                "LLM did not return a valid MessageOutputItem for the error page. Falling back to static."
            )
            return await _fallback_response()

        llm_response_text = output_item.content
        app_logger.info("Successfully received styled error page from LLM.")

        separator = None
        if "\r\n\r\n" in llm_response_text:
            separator = "\r\n\r\n"
        elif "\n\n" in llm_response_text:
            separator = "\n\n"

        if not separator:
            app_logger.error(
                "LLM-generated error page response is missing header-body separator. Falling back to static."
            )
            return await _fallback_response()

        header_section, body = llm_response_text.split(separator, 1)

        lines = header_section.split("\n")
        llm_headers = {}
        if lines:
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    llm_headers[key.strip()] = value.strip()

        return web.Response(
            text=body,
            status=status_code,
            content_type=llm_headers.get("Content-Type", "text/html; charset=utf-8"),
            headers=llm_headers,
        )

    except Exception as e:
        app_logger.exception(
            f"Failed to generate LLM error page, falling back to static template: {e}"
        )
        return await _fallback_response()


async def handle_http_request(request: web.Request) -> web.StreamResponse:
    start_time = time.perf_counter()
    client_address_tuple = request.transport.get_extra_info("peername")
    client_address_str = (
        f"{client_address_tuple[0]}:{client_address_tuple[1]}"
        if client_address_tuple
        else "Unknown Client"
    )
    access_logger.info(
        f"[{client_address_str}] Incoming request: {request.method} {request.path_qs}"
    )

    current_session_store: AbstractSessionStore = request.app["session_store"]
    raw_request_text = await _get_raw_request_aiohttp(request)

    session_id_from_cookie = None
    cookie_header = request.headers.get("Cookie")
    if cookie_header:
        try:
            cookies = http.cookies.SimpleCookie()
            cookies.load(cookie_header)
            if "X-Chat-Session-ID" in cookies:
                session_id_from_cookie = cookies["X-Chat-Session-ID"].value
                if session_id_from_cookie:
                    app_logger.info(
                        f"[{client_address_str}] Existing session ID found in cookie: {session_id_from_cookie}"
                    )
        except Exception:
            app_logger.exception(
                f"[{client_address_str}] Error parsing 'Cookie' header: '{cookie_header}'. Treating as no session."
            )

    system_prompt_template = request.app["system_prompt_template"]
    agent = request.app["agent"]
    global_state = request.app["global_state"]
    max_turns = request.app["max_turns"]
    context_window_max = request.app["context_window_max"]

    current_token_count = 0
    if session_id_from_cookie:
        current_token_count = await current_session_store.get_token_count(
            session_id_from_cookie
        )

    jinja_context = {
        "session_id": session_id_from_cookie or "",
        "global_state": json.dumps(global_state),
        "current_token_count": str(current_token_count),
        "context_window_max": str(context_window_max),
        "dynamic_date_example": formatdate(timeval=None, localtime=False, usegmt=True),
        "dynamic_server_name_example": "LLMWebServer/0.1",
    }

    try:
        template = jinja2.Template(system_prompt_template)
        dynamic_system_prompt = template.render(jinja_context)
    except jinja2.exceptions.TemplateSyntaxError as e:
        app_logger.exception(f"Jinja2 template syntax error in the system prompt: {e}")
        return await _send_llm_error_response_aiohttp(
            request,
            500,
            "Server Configuration Error",
            "Invalid system prompt template.",
        )

    messages = [
        {"role": "system", "content": dynamic_system_prompt},
        {"role": "user", "content": raw_request_text},
    ]

    app_logger.info(
        f"[{client_address_str}] Handing request to LLM with session context: "
        f"ID='{session_id_from_cookie or 'None'}', TokenCount={current_token_count}"
    )

    agent.instructions = None

    llm_call_start_time = None
    llm_first_token_time = None
    llm_stream_end_time = None
    llm_response_fully_collected_text_for_log = ""
    model_error_indicator_for_recording = None
    _last_chunk_finish_reason = None
    prompt_tokens_from_usage = 0
    completion_tokens_from_usage = 0

    response = None

    try:
        llm_call_start_time = time.perf_counter()
        app_logger.info(f"[{client_address_str}] Processing LLM request...")

        agent_stream = Runner.run_streamed(
            agent,
            messages,
            max_turns=max_turns,
        )

        llm_first_token_time = time.perf_counter()

        response = web.StreamResponse()
        response.enable_chunked_encoding(chunk_size=None)

        headers_and_status_parsed = False
        body_buffer = ""

        async for event in agent_stream.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                raw_chunk = event.data
                if (
                    hasattr(raw_chunk, "response")
                    and hasattr(raw_chunk.response, "usage")
                    and raw_chunk.response.usage
                ):
                    usage = raw_chunk.response.usage
                    app_logger.info(
                        f"[{client_address_str}] Usage found in stream chunk: {usage}"
                    )
                    prompt_tokens_from_usage += usage.input_tokens
                    completion_tokens_from_usage += usage.output_tokens

            if isinstance(event, RunItemStreamEvent):
                app_logger.debug(
                    f"[{client_address_str}] Stream event: {event.name} ({type(event.item)})"
                )

                if event.name in [
                    "message_chunk_created",
                    "message_output_created",
                ]:
                    chunk = ""
                    item = event.item
                    if hasattr(item, "chunk"):
                        if hasattr(item.chunk, "text"):
                            chunk = item.chunk.text
                    elif hasattr(item, "raw_item"):
                        if isinstance(item.raw_item.content, list):
                            for part in item.raw_item.content:
                                if hasattr(part, "text"):
                                    chunk = part.text
                                    break
                        elif hasattr(item.raw_item, "content"):
                            chunk = str(item.raw_item.content)

                    if not chunk:
                        continue

                    llm_response_fully_collected_text_for_log += chunk

                    if headers_and_status_parsed:
                        await response.write(chunk.encode("utf-8"))
                        continue

                    body_buffer += chunk

                    separator = None
                    if "\r\n\r\n" in body_buffer:
                        separator = "\r\n\r\n"
                    elif "\n\n" in body_buffer:
                        separator = "\n\n"

                    if separator:
                        header_section, body_part = body_buffer.split(separator, 1)
                        headers_and_status_parsed = True

                        lines = header_section.split("\n")
                        llm_status_code = 200
                        llm_headers = {}
                        if lines:
                            status_line = lines[0].strip()
                            if status_line.startswith("HTTP/"):
                                try:
                                    parts = status_line.split(" ", 2)
                                    if len(parts) >= 2:
                                        llm_status_code = int(parts[1])
                                except (ValueError, IndexError):
                                    app_logger.warning(
                                        f"Invalid status line: {status_line}"
                                    )
                            for line in lines[1:]:
                                if ":" in line:
                                    key, value = line.split(":", 1)
                                    llm_headers[key.strip()] = value.strip()

                        response.set_status(llm_status_code)
                        for k, v in llm_headers.items():
                            response.headers[k] = v
                        await response.prepare(request)
                        app_logger.info(
                            f"[{client_address_str}] Parsed HTTP headers from LLM, streaming response."
                        )

                        if body_part:
                            await response.write(body_part.encode("utf-8"))
            else:
                app_logger.debug(
                    f"[{client_address_str}] Stream event: {type(event).__name__}"
                )

        llm_stream_end_time = time.perf_counter()

        if not response.prepared:
            app_logger.warning(
                f"[{client_address_str}] LLM stream finished without a valid HTTP response header."
            )
            return await _send_llm_error_response_aiohttp(
                request,
                500,
                "Internal Server Error",
                "LLM did not produce a valid HTTP response.",
            )
        else:
            await response.write_eof()
            app_logger.info(
                f"[{client_address_str}] Successfully streamed full LLM response."
            )
            return response

    except Exception:
        app_logger.exception(
            f"[{client_address_str}] Unexpected error processing LLM stream:"
        )
        model_error_indicator_for_recording = "UNEXPECTED_STREAM_PROCESSING_ERROR"
        llm_response_fully_collected_text_for_log = "ERROR_UNEXPECTED_STREAM_PROCESSING"

        if response and not response.prepared:
            return await _send_llm_error_response_aiohttp(
                request,
                500,
                "Internal Server Error",
                "Unexpected error during stream processing.",
            )

        return response
    finally:
        final_session_id_for_turn = session_id_from_cookie
        cookie_match = re.search(
            r"Set-Cookie:\s*X-Chat-Session-ID=([^;]+)",
            llm_response_fully_collected_text_for_log,
            re.IGNORECASE,
        )
        if cookie_match:
            new_id = cookie_match.group(1).strip()
            if new_id != final_session_id_for_turn:
                app_logger.info(
                    f"New session ID '{new_id}' detected from LLM's Set-Cookie header. Adopting for logging."
                )
                final_session_id_for_turn = new_id

        if final_session_id_for_turn:
            await current_session_store.record_turn(
                final_session_id_for_turn, "user", raw_request_text
            )

            assistant_content_for_history = llm_response_fully_collected_text_for_log
            if model_error_indicator_for_recording:
                assistant_content_for_history = f"[LLM_RESPONSE_STREAM_INTERRUPTED_OR_ERROR: {model_error_indicator_for_recording}]\n\n{llm_response_fully_collected_text_for_log}"
            elif not llm_response_fully_collected_text_for_log.strip():
                assistant_content_for_history = "[LLM_EMPTY_RESPONSE_STREAMED]"

            await current_session_store.record_turn(
                final_session_id_for_turn,
                "assistant",
                assistant_content_for_history,
            )
            if prompt_tokens_from_usage > 0:
                await current_session_store.update_token_count(
                    final_session_id_for_turn, prompt_tokens_from_usage
                )
        else:
            app_logger.error(
                f"[{client_address_str}] Could not determine session ID for saving conversation turn. "
                "LLM may have failed to create a session or set a cookie."
            )

        end_time = time.perf_counter()
        duration = end_time - start_time
        ttft_str = "N/A"
        duration_llm_stream_str = "N/A"

        llm_ttft_seconds_val = None
        if llm_call_start_time and llm_first_token_time:
            ttft_calc = llm_first_token_time - llm_call_start_time
            if ttft_calc >= 0:
                llm_ttft_seconds_val = ttft_calc
                ttft_str = f"{llm_ttft_seconds_val:.3f}s"

        llm_stream_duration_seconds_val = None
        if llm_call_start_time and llm_stream_end_time:
            duration_llm_calc = llm_stream_end_time - llm_call_start_time
            if duration_llm_calc >= 0:
                llm_stream_duration_seconds_val = duration_llm_calc
                duration_llm_stream_str = f"{llm_stream_duration_seconds_val:.3f}s"

        compl_tokens_per_sec_str = "N/A"
        compl_tokens_per_sec_val = None
        if llm_stream_duration_seconds_val is not None:
            if llm_stream_duration_seconds_val > 0:
                if completion_tokens_from_usage > 0:
                    tokens_per_sec = (
                        completion_tokens_from_usage / llm_stream_duration_seconds_val
                    )
                    compl_tokens_per_sec_str = f"{tokens_per_sec:.2f}"
                    compl_tokens_per_sec_val = tokens_per_sec
                else:
                    compl_tokens_per_sec_str = "0.00 (no tokens)"
                    compl_tokens_per_sec_val = 0.0
            elif llm_stream_duration_seconds_val == 0:
                if completion_tokens_from_usage > 0:
                    compl_tokens_per_sec_str = "Infinity"
                    compl_tokens_per_sec_val = float("inf")
                else:
                    compl_tokens_per_sec_str = "0.00 (no tokens, instantaneous)"
                    compl_tokens_per_sec_val = 0.0

        log_msg_main_part = (
            f"[{client_address_str}] Request handled. "
            f"TotalDur: {duration:.3f}s, LLM_TTFT: {ttft_str}, LLM_StreamDur: {duration_llm_stream_str}, "
            f"PToken: {prompt_tokens_from_usage}, CToken: {completion_tokens_from_usage}, CTPS: {compl_tokens_per_sec_str}, "
            f"Sess: {final_session_id_for_turn}, "
            f"FinishReason: {_last_chunk_finish_reason if _last_chunk_finish_reason else 'N/A'}."
        )

        access_log_extra = {
            "client_address": client_address_str,
            "total_duration_seconds": round(duration, 3),
            "llm_ttft_seconds": round(llm_ttft_seconds_val, 3)
            if llm_ttft_seconds_val is not None
            else None,
            "llm_stream_duration_seconds": round(llm_stream_duration_seconds_val, 3)
            if llm_stream_duration_seconds_val is not None
            else None,
            "prompt_tokens": prompt_tokens_from_usage,
            "completion_tokens": completion_tokens_from_usage,
            "completion_tokens_per_second": round(compl_tokens_per_sec_val, 2)
            if compl_tokens_per_sec_val is not None
            and compl_tokens_per_sec_val != float("inf")
            else compl_tokens_per_sec_val,
            "session_hkey": final_session_id_for_turn,
            "session_log_id": final_session_id_for_turn,
            "new_session_by_server": final_session_id_for_turn
            != session_id_from_cookie,
            "http_method": request.method,
            "http_path_qs": request.path_qs,
            "llm_finish_reason": _last_chunk_finish_reason,
        }

        log_msg_final = log_msg_main_part
        if model_error_indicator_for_recording:
            access_log_extra["error_indicator"] = model_error_indicator_for_recording
            access_log_extra["llm_raw_response_on_error"] = (
                llm_response_fully_collected_text_for_log
            )
            log_msg_final += f" Error: {model_error_indicator_for_recording}."

        access_logger.info(log_msg_final, extra=access_log_extra)


async def on_startup(app: web.Application):
    """Async operations to perform on server startup."""
    # Initialize MCP servers and agent
    config = {
        "OPENAI_MODEL_NAME": app["openai_model_name"],
        "OPENAI_TEMPERATURE": app["openai_temperature"],
        "MCP_SERVERS": app.get("webapp_mcp_servers"),
        "OPENAI_BASE_URL": app.get("openai_base_url"),
        "API_KEY": app.get("api_key"),
        "LOCAL_TOOLS_ENABLED": app["local_tools_enabled"],
    }

    agent = await _initialize_mcp_servers_and_agent(config, app)
    app["agent"] = agent
    app_logger.info("Server startup actions completed.")


async def on_shutdown(app: web.Application):
    """Async operations to perform on server shutdown."""
    app_logger.info("\nServer shutting down (async)...")

    # Disconnect from MCP servers
    if app.get("mcp_server_lifecycles"):
        app_logger.info(
            f"Disconnecting from {len(app['mcp_server_lifecycles'])} MCP server(s)..."
        )
        for server in app["mcp_server_lifecycles"]:
            try:
                await server.__aexit__(None, None, None)
                app_logger.info(f"Disconnected from MCP server: {server.name}")
            except Exception as e:
                app_logger.exception(
                    f"Error disconnecting from MCP server {server.name}: {e}"
                )

    log_directory = "conversation_logs"
    current_session_store: AbstractSessionStore = app["session_store"]
    await current_session_store.save_all_sessions_on_shutdown(log_directory)

    app_logger.info("Server shutdown actions completed.")


def create_app(config: dict) -> web.Application:
    """Initializes and returns the aiohttp application."""
    session_store = InMemorySessionStore(save_to_disk=config["SAVE_CONVERSATIONS"])

    app = web.Application()
    app["global_state"] = {}
    app["system_prompt_template"] = config["SYSTEM_PROMPT_TEMPLATE"]
    app["openai_model_name"] = config["OPENAI_MODEL_NAME"]
    app["openai_temperature"] = config["OPENAI_TEMPERATURE"]
    app["port"] = config["PORT"]
    app["save_conversations"] = config["SAVE_CONVERSATIONS"]
    app["session_store"] = session_store
    app["api_key"] = config["API_KEY"]
    app["openai_base_url"] = config.get("OPENAI_BASE_URL")
    app["webapp_mcp_servers"] = config.get("MCP_SERVERS")
    app["max_turns"] = config["MAX_TURNS"]
    app["web_app_rules"] = config.get("WEB_APP_RULES", "")
    app["context_window_max"] = config["CONTEXT_WINDOW_MAX"]
    app["local_tools_enabled"] = config["LOCAL_TOOLS_ENABLED"]
    app["error_llm_system_prompt_template"] = config["ERROR_LLM_SYSTEM_PROMPT_TEMPLATE"]

    app.router.add_route("*", "/{path:.*}", handle_http_request)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


def run_local_tools_stdio_server():
    """Entry point for running the local tools server as a stdio MCP server."""
    # This server runs in its own process and has its own independent state.
    app_logger.info("Starting local tools stdio server...")
    # State is not saved to disk for the subprocess.
    session_store = InMemorySessionStore(save_to_disk=False)
    global_state = {}
    tools_app = create_local_tools_stdio_server(global_state, session_store)

    # The StdioServer's run() method is async and will run forever.
    try:
        tools_app.run(transport="stdio")
    except KeyboardInterrupt:
        app_logger.info("Local tools stdio server shut down by user.")
    finally:
        app_logger.info("Local tools stdio server has exited.")
