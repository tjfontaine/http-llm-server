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

"""
Main application module for the HTTP LLM Server.

This module contains the core request handler. The application factory
and startup/shutdown logic has been moved to the new orchestration system.
"""

import json
import time
from email.utils import formatdate
import os

import jinja2
from agents import Runner
from aiohttp import web

from .server.errors import send_llm_error_response_aiohttp
from .server.parsing import get_raw_request_aiohttp
from .server.streaming import LLMResponseStreamer
from .logging_config import get_loggers

# Initialize with default logging - will be reconfigured when config is available
app_logger, _, _ = get_loggers()


async def handle_http_request(request: web.Request) -> web.StreamResponse:
    """
    Simplified HTTP request handler.

    Most cross-cutting concerns (logging, session management, error handling)
    are now handled by middleware, making this handler focused on its core
    responsibility: coordinating the LLM request processing.

    Core responsibilities:
    - Prepare the system prompt with Jinja templating
    - Run the agent stream
    - Pass the stream to the LLMResponseStreamer
    - Return the response
    """
    # Handle health check immediately without LLM processing
    if request.path == "/_health_check":
        response = web.Response(
            text="OK",
            status=200,
            content_type="text/plain",
            headers={"Cache-Control": "no-cache"},
        )
        return response

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
    web_app_file = config.web_app_file
    web_app_dir = (
        os.path.dirname(os.path.abspath(web_app_file)) if web_app_file else os.getcwd()
    )

    # First, render the web_app_rules, which may also be a Jinja template
    rendered_rules = ""
    if config.web_app_rules:
        try:
            rules_template = jinja2.Template(config.web_app_rules)
            # The rules template may need the web app directory
            rendered_rules = rules_template.render({"WEB_APP_DIR": web_app_dir})
        except jinja2.exceptions.TemplateSyntaxError as e:
            app_logger.warning(
                f"Jinja2 template syntax error in web_app_rules: {e}. Using raw rules."
            )
            rendered_rules = config.web_app_rules

    # Prepare the debug panel prompt if debug mode is enabled
    debug_panel_prompt = ""
    if config.debug:
        debug_panel_prompt = request.app.get("debug_panel_prompt", "")
        app_logger.info(
            f"[{client_address_str}] Debug mode is active. Injecting debug panel prompt."
        )

    jinja_context = {
        "session_id": session_id_from_cookie or "",
        "global_state": json.dumps(global_state, indent=2),
        "current_token_count": str(current_token_count),
        "context_window_max": str(context_window_max),
        "dynamic_date_example": formatdate(timeval=None, localtime=False, usegmt=True),
        "dynamic_server_name_example": "LLMWebServer/0.1",
        "WEB_APP_DIR": web_app_dir,
        "web_app_rules": rendered_rules,
        "debug_panel_prompt": debug_panel_prompt,
        "history_json": json.dumps(history, indent=2),
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
        f"[{client_address_str}] Processing request: {session_id_from_cookie or 'new session'}, "
        f"History: {len(history)} turns, Tokens: {current_token_count}"
    )

    # Reset agent instructions and start LLM processing
    agent.instructions = None
    app_logger.debug(
        f"[{client_address_str}] Agent instructions set to: {agent.instructions}"
    )

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

    # Log the system prompt and message structure at debug level
    app_logger.debug(
        f"[{client_address_str}] System prompt length: {len(dynamic_system_prompt)} chars"
    )
    app_logger.debug(
        f"[{client_address_str}] Messages: {len(messages)} total, roles: {[msg.get('role', 'unknown') for msg in messages]}"
    )

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
            f"[{client_address_str}] LLM stream finished without valid HTTP response headers"
        )
        return await send_llm_error_response_aiohttp(
            request,
            500,
            "Internal Server Error",
            "LLM did not produce a valid HTTP response.",
        )
    else:
        app_logger.debug(f"[{client_address_str}] Successfully streamed LLM response")

    return response
