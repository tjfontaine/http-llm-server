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

import http.cookies
import http.server
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
    raw_request_text = await get_raw_request_aiohttp(request)

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

    # Load typed config
    config = request.app["config"]
    system_prompt_template = config.system_prompt_template
    agent = request.app["agent"]
    global_state = request.app["global_state"]
    max_turns = config.max_turns
    context_window_max = config.context_window_max

    history = []
    current_token_count = 0
    if session_id_from_cookie:
        full_history = await current_session_store.get_history(session_id_from_cookie)
        # Strip extra keys from history that the LLM API might reject
        history = [
            {"role": turn["role"], "content": turn["content"]}
            for turn in full_history.messages
        ]
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
        return await send_llm_error_response_aiohttp(
            request,
            500,
            "Server Configuration Error",
            "Invalid system prompt template.",
        )

    messages = [{"role": "system", "content": dynamic_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": raw_request_text})

    app_logger.info(
        f"[{client_address_str}] Handing request to LLM with session context: "
        f"ID='{session_id_from_cookie or 'None'}', "
        f"HistoryTurns={len(history)}, TokenCount={current_token_count}"
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
    # Use a local variable for session state to handle cases where the session ID is assigned mid-stream.
    final_session_id_for_turn = session_id_from_cookie

    try:
        llm_call_start_time = time.perf_counter()
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

        agent_stream = Runner.run_streamed(
            agent,
            messages,
            max_turns=max_turns,
        )

        # Delegate the complex streaming logic to the dedicated streamer class
        streamer = LLMResponseStreamer(client_address_str)
        response, final_session_id_for_turn, metrics = await streamer.stream_response(
            request, agent_stream, max_turns, session_id_from_cookie
        )

        # Extract metrics from the streamer
        llm_response_fully_collected_text_for_log = metrics[
            "llm_response_fully_collected_text_for_log"
        ]
        model_error_indicator_for_recording = metrics[
            "model_error_indicator_for_recording"
        ]
        _last_chunk_finish_reason = metrics["_last_chunk_finish_reason"]
        prompt_tokens_from_usage = metrics["prompt_tokens_from_usage"]
        completion_tokens_from_usage = metrics["completion_tokens_from_usage"]
        llm_first_token_time = metrics["llm_first_token_time"]
        llm_stream_end_time = metrics["llm_stream_end_time"]

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

    except Exception:
        app_logger.exception(
            f"[{client_address_str}] Unexpected error processing LLM stream:"
        )
        model_error_indicator_for_recording = "UNEXPECTED_STREAM_PROCESSING_ERROR"
        llm_response_fully_collected_text_for_log = "ERROR_UNEXPECTED_STREAM_PROCESSING"

        if response and not response.prepared:
            return await send_llm_error_response_aiohttp(
                request,
                500,
                "Internal Server Error",
                "Unexpected error during stream processing.",
            )

        return response
    finally:
        # This block ensures that critical post-request actions are always performed,
        # such as recording the conversation turn and logging detailed performance metrics.
        # This is vital for debugging, monitoring, and maintaining session state.
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

        # Debug timing and response characteristics
        app_logger.debug(
            f"[{client_address_str}] Request processing complete",
            extra={
                "total_duration": f"{duration:.3f}s",
                "llm_ttft": ttft_str,
                "llm_stream_duration": duration_llm_stream_str,
                "tokens_per_second": compl_tokens_per_sec_str,
                "response_size_chars": len(llm_response_fully_collected_text_for_log),
                "session_id": final_session_id_for_turn or "none",
                "had_errors": bool(model_error_indicator_for_recording),
            },
        )

        access_log_extra = {
            "remote_address": client_address_str,
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

        log_msg_final = f"[{client_address_str}] Request handled. "
        log_msg_final += f"TotalDur: {duration:.3f}s, LLM_TTFT: {ttft_str}, LLM_StreamDur: {duration_llm_stream_str}, "
        log_msg_final += f"PToken: {prompt_tokens_from_usage}, CToken: {completion_tokens_from_usage}, CTPS: {compl_tokens_per_sec_str}, "
        log_msg_final += f"Sess: {final_session_id_for_turn}, "
        log_msg_final += f"FinishReason: {_last_chunk_finish_reason if _last_chunk_finish_reason else 'N/A'}."

        if model_error_indicator_for_recording:
            access_log_extra["error_indicator"] = model_error_indicator_for_recording
            access_log_extra["llm_raw_response_on_error"] = (
                llm_response_fully_collected_text_for_log
            )
            log_msg_final += f" Error: {model_error_indicator_for_recording}."

        access_logger.info(log_msg_final, extra=access_log_extra)


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

    app = web.Application()
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
