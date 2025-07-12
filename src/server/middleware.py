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
Middleware module for aiohttp application.

This module provides middlewares for:
- Logging and metrics collection (request timing, token counting)
- Session management (cookie extraction and session attachment)
- Error handling (LLM-generated error responses)
"""

import time
from typing import Awaitable, Callable

from aiohttp import web

from ..logging_config import get_loggers
from .errors import send_llm_error_response_aiohttp

# Get loggers for consistent logging throughout the application
app_logger, access_logger, _ = get_loggers()

EMPTY_RESPONSE_STREAMED = "[LLM_EMPTY_RESPONSE_STREAMED]"


def logging_and_metrics_middleware():
    """
    Middleware factory for logging requests and collecting metrics.

    Handles:
    - Request timing (TTFT, total duration)
    - Token counting and completion metrics
    - Final structured logging with performance data
    """

    @web.middleware
    async def middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        start_time = time.perf_counter()
        client_address_str = f"{request.remote}"
        request["client_address_str"] = client_address_str
        access_logger.info(
            f"[{client_address_str}] Incoming: {request.method} {request.path_qs}"
        )

        try:
            response = await handler(request)

            # Extract metrics from request context (set by handler or other middleware)
            llm_first_token_time = request.get("llm_first_token_time")
            llm_stream_end_time = request.get("llm_stream_end_time")
            llm_call_start_time = request.get("llm_call_start_time")
            llm_response_fully_collected_text_for_log = request.get(
                "llm_response_fully_collected_text_for_log", ""
            )
            model_error_indicator_for_recording = request.get(
                "model_error_indicator_for_recording"
            )
            last_chunk_finish_reason = request.get("last_chunk_finish_reason")
            prompt_tokens_from_usage = request.get("prompt_tokens_from_usage", 0)
            completion_tokens_from_usage = request.get(
                "completion_tokens_from_usage", 0
            )
            final_session_id_for_turn = request.get("final_session_id_for_turn")
            session_id_from_cookie = request.get("session_id_from_cookie")

            # Calculate performance metrics
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
                            completion_tokens_from_usage
                            / llm_stream_duration_seconds_val
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
                    "response_size_chars": len(
                        llm_response_fully_collected_text_for_log or ""
                    ),
                    "session_id": final_session_id_for_turn or "none",
                    "had_errors": bool(model_error_indicator_for_recording),
                    "finish_reason": last_chunk_finish_reason or "none",
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
                "http_status": response.status,
                "llm_finish_reason": last_chunk_finish_reason,
            }

            log_msg_final = f"[{client_address_str}] {request.method} {request.path_qs} - {response.status}"

            if model_error_indicator_for_recording:
                log_msg_final += " [MODEL_ERROR]"
                access_log_extra["error_indicator"] = (
                    model_error_indicator_for_recording
                )
                access_log_extra["llm_raw_response_on_error"] = (
                    llm_response_fully_collected_text_for_log
                )

            access_logger.info(log_msg_final, extra=access_log_extra)

            return response
        except Exception as e:
            app_logger.exception(
                f"[{client_address_str}] Unhandled exception in request handler: {e}"
            )

            # Store error information for logging middleware
            request["model_error_indicator_for_recording"] = "UNHANDLED_EXCEPTION"
            request["llm_response_fully_collected_text_for_log"] = f"ERROR: {str(e)}"

            return await send_llm_error_response_aiohttp(
                request,
                request.app["agent"],
                500,
                "Internal Server Error",
                f"An unexpected error occurred: {str(e)}",
            )

    return middleware


def session_middleware():
    """
    Middleware factory for session management.

    Extracts session ID from cookies and attaches session information to the request.
    Stores session ID and session data on the request object for use by handlers.
    """

    @web.middleware
    async def middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        client_address_str = request.get("client_address_str", "Unknown Client")

        session_id_from_cookie = None
        if "Cookie" in request.headers:
            cookie_header = request.headers["Cookie"]
            try:
                # Basic parsing, not a full cookie parser
                if "session_id" in cookie_header:
                    session_id_from_cookie = cookie_header.split("session_id=")[
                        1
                    ].split(";")[0]
                    if session_id_from_cookie:
                        app_logger.info(
                            "Existing session ID from cookie",
                            extra={
                                "client_address": client_address_str,
                                "session_id": session_id_from_cookie,
                            },
                        )
            except Exception:
                app_logger.exception(
                    "Error parsing 'Cookie' header, treating as no session.",
                    extra={
                        "client_address": client_address_str,
                        "header": cookie_header,
                    },
                )

        # Store session information on request
        request["session_id_from_cookie"] = session_id_from_cookie

        # Session history and token count are now managed by the agent's session memory
        request["llm_history"] = []
        request["session_token_count"] = 0

        response = await handler(request)

        final_session_id_for_turn = request.get("final_session_id_for_turn")

        # Set cookie if a new session ID was generated
        if (
            final_session_id_for_turn
            and final_session_id_for_turn != session_id_from_cookie
        ):
            app_logger.info(
                "Setting new session cookie",
                extra={
                    "client_address": client_address_str,
                    "session_id": final_session_id_for_turn,
                },
            )
            response.set_cookie(
                "session_id",
                final_session_id_for_turn,
                httponly=True,
                samesite="Lax",
                path="/",
            )

        return response

    return middleware


def error_handling_middleware():
    """
    Middleware factory for error handling.

    Wraps the handler call in a try/except block and invokes the LLM error
    page generator for unhandled exceptions.
    """

    @web.middleware
    async def middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            response = await handler(request)
            # If the response is an error response, the body is already set
            # by send_llm_error_response_aiohttp. We just return it.
            if response.status >= 400:
                app_logger.warning(
                    f"Request to {request.path} resulted in status {response.status}"
                )
            return response
        except web.HTTPException as ex:
            # Handle known aiohttp exceptions
            app_logger.warning(f"HTTP exception for {request.path}: {ex}")
            return await send_llm_error_response_aiohttp(
                request,
                request.app["agent"],
                ex.status,
                ex.reason,
                ex.text,
            )
        except Exception as e:
            # Fallback for unhandled exceptions
            app_logger.exception(f"Unhandled exception in handler for {request.path}")
            return await send_llm_error_response_aiohttp(
                request,
                request.app["agent"],
                500,
                "Internal Server Error",
                f"An unexpected error occurred: {str(e)}",
            )

    return middleware


def session_cleanup_middleware():
    """
    Middleware factory for cleaning up session data after a request.

    This middleware is responsible for persisting session data if needed.
    """

    @web.middleware
    async def middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        response = await handler(request)

        # Session persistence is now handled by the agent's SQLite session memory.
        # The logic for saving conversation history to request.app["sessions"]
        # has been removed.

        return response

    return middleware
