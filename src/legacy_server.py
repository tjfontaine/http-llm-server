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

import json
import time
from email.utils import formatdate

import jinja2
from agents import (
    Runner,
)
from aiohttp import web

from .local_tools import create_local_tools_stdio_server
from src.config import Config
from .logging_config import configure_logging, get_loggers
from .server.session import AbstractSessionStore, InMemorySessionStore
from .server.parsing import get_raw_request_aiohttp
from .server.agent_setup import initialize_mcp_servers_and_agent
from .server.streaming import LLMResponseStreamer
from .server.errors import send_llm_error_response_aiohttp
from .server.middleware import (
    logging_and_metrics_middleware,
    session_middleware,
    error_handling_middleware,
    session_cleanup_middleware,
)


# Default web app file path
DEFAULT_WEB_APP_FILE = "examples/default_info_site/prompt.md"


# --- Main Application ---
# --- Static Prompts and Global State ---
LLM_HTTP_SERVER_PROMPT_BASE = ""  # This will be loaded from a file


# This is loaded by main.py and passed in the config
# ERROR_LLM_SYSTEM_PROMPT_TEMPLATE = ""


# --- Logging Configuration (Global for simplicity, initialized early) ---
# Initialize with default logging - will be reconfigured when config is available
app_logger, access_logger, conversation_logger = get_loggers()


async def handle_http_request(request: web.Request) -> web.StreamResponse:
    """
    Simplified HTTP request handler.

    Most cross-cutting concerns (logging, session management, error handling)
    are now handled by middleware, making this handler focused on its core
    responsibility: coordinating the LLM request processing.
    """
    # Get data from middleware
    client_address_str = request["client_address_str"]
    session_id_from_cookie = request["session_id_from_cookie"]
    history = request["llm_history"]
    current_token_count = request["session_token_count"]

    # Get raw request text
    raw_request_text = await get_raw_request_aiohttp(request)
    request["raw_request_text"] = raw_request_text  # Store for middleware use

    # Load typed config and app state
    config = request.app["config"]
    system_prompt_template = config.system_prompt_template
    agent = request.app["agent"]
    global_state = request.app["global_state"]
    max_turns = config.max_turns
    context_window_max = config.context_window_max

    # Prepare Jinja context for system prompt
    jinja_context = {
        "session_id": session_id_from_cookie or "",
        "global_state": json.dumps(global_state),
        "current_token_count": str(current_token_count),
        "context_window_max": str(context_window_max),
        "dynamic_date_example": formatdate(timeval=None, localtime=False, usegmt=True),
        "dynamic_server_name_example": "LLMWebServer/0.1",
    }

    # Render system prompt
    try:
        template = jinja2.Template(system_prompt_template)
        dynamic_system_prompt = template.render(jinja_context)
    except jinja2.exceptions.TemplateSyntaxError as e:
        app_logger.exception(f"Jinja2 template syntax error in the system prompt: {e}")
        return await send_llm_error_response_aiohttp(
            request,
            500,
            "Server Configuration Error",
            "Invalid system prompt template.",
        )

    # Prepare messages for LLM
    messages = [{"role": "system", "content": dynamic_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": raw_request_text})

    app_logger.info(
        f"[{client_address_str}] Handing request to LLM with session context: "
        f"ID='{session_id_from_cookie or 'None'}', "
        f"HistoryTurns={len(history)}, TokenCount={current_token_count}"
    )

    # Reset agent instructions and start LLM processing
    agent.instructions = None

    # Store timing for middleware
    llm_call_start_time = time.perf_counter()
    request["llm_call_start_time"] = llm_call_start_time

    app_logger.debug(
        f"[{client_address_str}] Starting LLM request processing",
        extra={
            "session_id": session_id_from_cookie or "new",
            "history_turns": len(history),
            "token_count": current_token_count,
            "max_turns": max_turns,
        },
    )
    app_logger.info(f"[{client_address_str}] Processing LLM request...")

    # Run the LLM stream
    agent_stream = Runner.run_streamed(
        agent,
        messages,
        max_turns=max_turns,
    )

    # Delegate streaming to the dedicated streamer class
    streamer = LLMResponseStreamer(client_address_str)
    response, final_session_id_for_turn, metrics = await streamer.stream_response(
        request, agent_stream, max_turns, session_id_from_cookie
    )

    # Store metrics and session data on request for middleware use
    request["llm_response_fully_collected_text_for_log"] = metrics[
        "llm_response_fully_collected_text_for_log"
    ]
    request["model_error_indicator_for_recording"] = metrics[
        "model_error_indicator_for_recording"
    ]
    request["last_chunk_finish_reason"] = metrics["_last_chunk_finish_reason"]
    request["prompt_tokens_from_usage"] = metrics["prompt_tokens_from_usage"]
    request["completion_tokens_from_usage"] = metrics["completion_tokens_from_usage"]
    request["llm_first_token_time"] = metrics["llm_first_token_time"]
    request["llm_stream_end_time"] = metrics["llm_stream_end_time"]
    request["final_session_id_for_turn"] = final_session_id_for_turn

    # Validate response
    if not response.prepared:
        app_logger.warning(
            f"[{client_address_str}] LLM stream finished without a valid HTTP response header."
        )
        return await send_llm_error_response_aiohttp(
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


async def on_startup(app: web.Application):
    """Initialize application state and connections."""
    config: Config = app["config"]
    app_logger.info("Server is starting up...")
    app["start_time"] = time.time()
    await initialize_mcp_servers_and_agent(config, app)
    app_logger.info("Server startup complete.")


async def on_shutdown(app: web.Application):
    """Async operations to perform on server shutdown."""
    app_logger.info("\nServer shutting down (async)...")

    # Disconnect from MCP servers
    if app.get("mcp_server_lifecycles"):
        app_logger.info(
            f"Closing {len(app['mcp_server_lifecycles'])} MCP server connections..."
        )
        for mcp_server in app["mcp_server_lifecycles"]:
            try:
                await mcp_server.close()
                app_logger.info(f"Closed MCP server: {mcp_server.params}")
            except Exception as e:
                app_logger.error(
                    f"Error closing MCP server {mcp_server.params}: {e}",
                    exc_info=True,
                )

    log_directory = "conversation_logs"
    current_session_store: AbstractSessionStore = app["session_store"]
    await current_session_store.save_all_sessions_on_shutdown(log_directory)

    app_logger.info("Server shutdown actions completed.")


def create_app(config: Config) -> web.Application:
    """
    Create and configure the main aiohttp application.
    """
    # Reconfigure logging with the specified level
    configure_logging(config.log_level)

    # Typed config from Pydantic
    session_store = InMemorySessionStore(save_to_disk=config.save_conversations)

    # Register middlewares in the order they should execute
    # Note: aiohttp middlewares are executed in reverse order of registration
    # So the last registered middleware is the first to run
    app = web.Application(
        middlewares=[
            logging_and_metrics_middleware(),  # Last - handles final logging and metrics
            session_cleanup_middleware(),  # Third - records session data after handler
            error_handling_middleware(),  # Second - catches errors from handler
            session_middleware(),  # First - extracts session data from request
        ]
    )
    app["global_state"] = {}
    app["config"] = config
    app["session_store"] = session_store
    app["error_llm_system_prompt_template"] = config.error_llm_system_prompt_template

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

    # The StdioServer's run() method is async and will run until the process is terminated.
    try:
        tools_app.run(transport="stdio")
    except KeyboardInterrupt:
        app_logger.info("Local tools stdio server shut down by user.")
    finally:
        app_logger.info("Local tools stdio server has exited.")
