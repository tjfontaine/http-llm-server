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
Error handling module for LLM-generated error responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.items import MessageOutputItem
from aiohttp import web

from src.logging_config import get_loggers

if TYPE_CHECKING:
    from agents import Agent

# Get logger for error handling
app_logger, _, _ = get_loggers()


async def send_llm_error_response_aiohttp(
    request: web.Request,
    agent: Agent,
    status_code: int,
    message: str,
    error_details: str,
) -> web.Response:
    """
    Generates and sends a styled error page using an LLM.

    Args:
        request: The original aiohttp request.
        agent: The agent instance to use for generating the error page.
        status_code: The HTTP status code for the error.
        message: A brief, user-friendly error message.
        error_details: A more detailed, technical description of the error.

    Returns:
        An aiohttp Response object with the error page
    """
    if not agent:
        app_logger.warning(
            "Agent not available for LLM-generated error page. Falling back."
        )
        return await _minimal_fallback_response(status_code, message, error_details)

    try:
        app_logger.info(
            f"Attempting to generate a styled error page with LLM for {status_code}..."
        )
        error_llm_system_prompt_template = request.app.get(
            "error_llm_system_prompt_template"
        )
        if not error_llm_system_prompt_template:
            app_logger.error(
                "Error LLM system prompt template not found. Falling back."
            )
            return await _minimal_fallback_response(status_code, message, error_details)

        system_prompt = error_llm_system_prompt_template.render(status_code=status_code)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Please generate the HTTP response for the "
                f"{status_code} error page now.",
            },
        ]

        async with agent.runner.create_run_stream(messages=messages) as stream:
            response_text = ""
            async for output_item in stream:
                if (
                    not isinstance(output_item, MessageOutputItem)
                    or not output_item.content
                ):
                    app_logger.error(
                        "LLM did not return a valid MessageOutputItem for the error "
                        "page. Falling back."
                    )
                    return await _minimal_fallback_response(
                        status_code, message, error_details
                    )
                response_text += output_item.content

            if not response_text:
                app_logger.error(
                    "LLM returned an empty response for the error page. Falling back."
                )
                return await _minimal_fallback_response(
                    status_code, message, error_details
                )

            separator = "\r\n\r\n"
            if separator not in response_text:
                app_logger.error(
                    "LLM-generated error page response is missing header-body "
                    "separator. Falling back."
                )
                return await _minimal_fallback_response(
                    status_code, message, error_details
                )

            header_text, body = response_text.split(separator, 1)
            response = web.Response(status=status_code, body=body)
            for line in header_text.split("\r\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    response.headers[key.strip()] = value.strip()

            app_logger.info(f"Successfully generated LLM error page for {status_code}")
            return response

    except Exception as e:
        app_logger.exception(
            f"Failed to generate LLM error page, falling back to minimal template: {e}"
        )
        return await _minimal_fallback_response(status_code, message, error_details)


async def _minimal_fallback_response(
    status_code: int, message: str, error_details: str
):
    """
    This is the last resort, plain text response
    """
    fallback_body = f"HTTP {status_code} - {message}\n\nError Details: {error_details}"
    return web.Response(
        text=fallback_body,
        status=status_code,
        content_type="text/plain",
        charset="utf-8",
        headers={"Connection": "close"},
    )
