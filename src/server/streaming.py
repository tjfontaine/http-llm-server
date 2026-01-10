# src/server/streaming.py
import asyncio
from typing import Any, AsyncGenerator

from agents import Agent
from agents.items import RunItem, ToolCallItem, ToolCallOutputItem
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from src.logging_config import get_loggers

app_logger, _, _ = get_loggers()


class StreamingContext:
    def __init__(self, request: web.Request, agent: Agent):
        self.request = request
        self.agent = agent
        self.response = web.StreamResponse()
        self.client_address_str = f"{request.remote}"
        self.body_buffer = ""
        self.header_section = ""
        self.headers_and_status_parsed = False
        self.llm_response_fully_collected_text_for_log = ""
        self.model_error_indicator_for_recording: str | None = None
        self.separator = "\r\n\r\n"
        self.llm_first_token_time: float | None = None
        self.llm_stream_end_time: float | None = None
        self.prompt_tokens_from_usage = 0
        self.completion_tokens_from_usage = 0
        self.total_tokens_from_usage = 0
        self._last_chunk_finish_reason: str | None = None
        self.session_id_from_tool_call: str | None = None

    async def stream_agent_response(
        self, agent_stream: AsyncGenerator[Any, None]
    ) -> tuple[web.StreamResponse, dict[str, Any]]:
        # The core of this server: stream the agent's response.
        # This loop processes events from the agent, including tool calls and content
        # chunks. A key design element is that the LLM is expected to generate a
        # raw HTTP response, which this server parses on-the-fly to construct a
        # valid `aiohttp.web.Response`.
        async for event in agent_stream.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                # Handle different types of response events
                from openai.types.responses import (
                    ResponseCompletedEvent,
                    ResponseTextDeltaEvent,
                )
                from openai.types.responses.response_created_event import (
                    ResponseCreatedEvent,
                )

                if isinstance(event.data, ResponseTextDeltaEvent):
                    # Handle text delta events (streaming content)
                    if event.data.delta:
                        await self.process_chunk(event.data.delta)
                elif isinstance(event.data, ResponseCompletedEvent):
                    # Handle completed response events (final response with usage info)
                    if event.data.response.usage:
                        usage = event.data.response.usage
                        app_logger.info(
                            "Usage in completed response",
                            extra={
                                "client_address": self.client_address_str,
                                "usage": usage,
                            },
                        )
                        self.prompt_tokens_from_usage += usage.input_tokens or 0
                        self.completion_tokens_from_usage += usage.output_tokens or 0
                        self.total_tokens_from_usage += usage.total_tokens or 0

                        # Get finish reason from the first choice if available
                        if (
                            event.data.response.output
                            and len(event.data.response.output) > 0
                        ):
                            first_output = event.data.response.output[0]
                            if hasattr(first_output, "finish_reason"):
                                self._last_chunk_finish_reason = (
                                    first_output.finish_reason
                                )

                    # For completed responses, we also need to process the full
                    # response content in case it wasn't streamed in deltas
                    if (
                        event.data.response.output
                        and len(event.data.response.output) > 0
                    ):
                        first_output = event.data.response.output[0]
                        if hasattr(first_output, "content") and first_output.content:
                            for content_item in first_output.content:
                                if hasattr(content_item, "text") and content_item.text:
                                    # Only process if we haven't received this content
                                    # via deltas
                                    collected = (
                                        self.llm_response_fully_collected_text_for_log
                                    )
                                    if not collected:
                                        await self.process_chunk(content_item.text)
                elif isinstance(event.data, ResponseCreatedEvent):
                    # Handle response created events (initial response setup)
                    app_logger.debug(
                        "Response created",
                        extra={
                            "client_address": self.client_address_str,
                            "response_id": event.data.response.id,
                        },
                    )
                # Handle other event types as needed
                else:
                    app_logger.debug(
                        "Unhandled raw response event type",
                        extra={
                            "client_address": self.client_address_str,
                            "event_type": type(event.data).__name__,
                        },
                    )
            elif isinstance(event, RunItemStreamEvent):
                if event.name == "tool_called":
                    await self.handle_tool_calls(event.item)
                elif event.name == "tool_output":
                    await self.handle_tool_results(event.item)

        # After the stream finishes, handle any remaining buffered content
        if not self.response.prepared:
            if self.body_buffer:
                # If stream finishes with content but no headers, treat as a simple
                # 200 OK response with the buffered content as the body.
                app_logger.warning(
                    "Stream finished with content but no headers. "
                    "Serving as 200 OK with text/plain."
                )
                self.response.set_status(200)
                self.response.headers['Content-Type'] = 'text/plain; charset=utf-8'
                await self.response.prepare(self.request)
                await self.response.write(self.body_buffer.encode('utf-8'))
            else:
                # If stream finishes with no headers and no content, it's likely
                # an error or an empty response was intended.
                app_logger.warning(
                    "Stream finished but HTTP response headers were not finalized "
                    "and no content was buffered."
                )

        # Prepare metrics dictionary
        metrics = {
            "llm_response_fully_collected_text_for_log": (
                self.llm_response_fully_collected_text_for_log
            ),
            "model_error_indicator_for_recording": (
                self.model_error_indicator_for_recording
            ),
            "_last_chunk_finish_reason": self._last_chunk_finish_reason,
            "prompt_tokens_from_usage": self.prompt_tokens_from_usage,
            "completion_tokens_from_usage": self.completion_tokens_from_usage,
            "total_tokens_from_usage": self.total_tokens_from_usage,
        }
        # Note: Removed set_tcp_cork call since StreamResponse doesn't have this method
        return self.response, metrics

    async def handle_tool_calls(self, item: RunItem):
        app_logger.debug(
            "Handling tool calls",
            extra={
                "client_address": self.client_address_str,
                "item": item,
            },
        )
        if isinstance(item, ToolCallItem):
            raw_tool_call = item.raw_item
            if hasattr(raw_tool_call, "function") and raw_tool_call.function:
                function_name = getattr(raw_tool_call.function, "name", "unknown")
                # Log tool call at DEBUG level with function name
                app_logger.debug(
                    "Tool called",
                    extra={
                        "function_name": function_name,
                        "client_address": self.client_address_str,
                    },
                )
                if function_name == "create_session":
                    # The create_session tool returns the new session ID directly
                    # We need to extract it from the result when it's available
                    # For now, we'll handle this in the tool result processing
                    pass
        return None

    async def handle_tool_results(self, item: ToolCallOutputItem):
        """Processes a tool result and writes it to the stream."""
        # Extract function name from raw_item if available
        # In openai-agents 0.6+, raw_item may be a dict or FunctionCallOutput
        function_name = "unknown"
        raw_item = item.raw_item
        if isinstance(raw_item, dict):
            function_name = raw_item.get("name", raw_item.get("call_id", "unknown"))
        elif hasattr(raw_item, "call_id"):
            function_name = getattr(raw_item, "call_id", "unknown")
        
        output = item.output
        
        # Debug: log the output structure
        # print(f"DEBUG OUTPUT: type={type(output).__name__}")
        # print(f"DEBUG OUTPUT: output={output!r}")
        
        # Extract text content from output
        # The output can be:
        # 1. A JSON string like {"type":"text","text":"...","annotations":null}
        # 2. An object with .content[0].text
        # 3. A plain string
        text_content = ""
        if isinstance(output, str):
            # Try to parse as JSON 
            if output.startswith("{"):
                import json
                try:
                    parsed = json.loads(output)
                    if isinstance(parsed, dict) and "text" in parsed:
                        text_content = parsed["text"]
                    else:
                        text_content = output
                except json.JSONDecodeError:
                    text_content = output
            else:
                text_content = output
        elif hasattr(output, 'content') and output.content:
            try:
                text_content = output.content[0].text
            except (KeyError, IndexError, AttributeError):
                text_content = str(output)
        else:
            text_content = str(output)
        
        app_logger.info(
            "Tool result | function_name=%s text=%s client_address=%s",
            function_name,
            text_content[:100] if len(text_content) > 100 else text_content,
            self.request.remote,
        )
        
        # Detect tool type by examining the extracted text content
        # HTTP responses start with "HTTP/"
        if text_content.startswith("HTTP/"):
            # This is an HTTP response from generate_http_response
            # Clear any prior LLM text output from the buffer that shouldn't be
            # part of the HTTP response (e.g., explanatory text the LLM generated
            # before calling the tool)
            if self.body_buffer and not self.response.prepared:
                app_logger.debug(
                    "Clearing body_buffer before HTTP tool response",
                    extra={
                        "cleared_content": self.body_buffer[:100],
                        "client_address": self.client_address_str,
                    }
                )
                self.body_buffer = ""
            await self.process_chunk(text_content)
            
        elif len(text_content) < 100 and "-" in text_content:
            # This looks like a session ID (UUID format)
            session_id = text_content.strip()
            self.session_id_from_tool_call = session_id
            app_logger.info(
                "Session ID extracted from tool call",
                extra={
                    "session_id": session_id,
                    "client_address": self.client_address_str,
                }
            )


    async def process_chunk(self, chunk: str):
        # This method is crucial for on-the-fly parsing of the LLM's output.
        # It buffers data, detects the header-body separator, and switches from
        # parsing headers to streaming the body.
        try:
            if not self.response.prepared:
                # Buffer until we find the separator
                self.body_buffer += chunk
                self.llm_response_fully_collected_text_for_log += chunk

                # Check for both possible separators
                separator_found = None
                if self.separator in self.body_buffer:
                    separator_found = self.separator
                elif "\n\n" in self.body_buffer:
                    separator_found = "\n\n"

                if separator_found:
                    header_section, self.body_buffer = self.body_buffer.split(
                        separator_found, 1
                    )
                    self.header_section = header_section
                    app_logger.info(
                        "Parsing HTTP headers from LLM",
                        extra={
                            "client_address": self.client_address_str,
                            "header_snippet": (
                                f"{header_section[:200]}"
                                f"{'...' if len(header_section) > 200 else ''}"
                            ),
                        },
                    )
                    await self._parse_and_prepare_response(
                        header_section, self.body_buffer
                    )
                else:
                    app_logger.debug(
                        "No header separator found yet, continuing to buffer",
                        extra={"client_address": self.client_address_str},
                    )
            else:
                # Once headers are parsed, stream the rest of the body
                await self.response.write(chunk.encode("utf-8"))

        except (ClientConnectionResetError, ConnectionResetError):
            app_logger.warning(
                "[%s] Client disconnected before response was complete.",
                self.client_address_str,
            )
            self.model_error_indicator_for_recording = "client_disconnected"
            # Stop further processing by raising a special exception or by returning
            raise asyncio.CancelledError("Client disconnected")

    async def _parse_and_prepare_response(
        self, header_section: str, initial_body_chunk: str
    ):
        # This method parses the HTTP status line and headers and prepares the
        # aiohttp.web.StreamResponse.
        try:
            # Handle both \r\n and \n line endings
            if "\r\n" in header_section:
                lines = header_section.split("\r\n")
            else:
                lines = header_section.split("\n")

            status_line = lines[0]

            # Parse status line: "HTTP/1.1 200 OK"
            parts = status_line.strip().split(" ", 2)
            if len(parts) >= 3:
                version, status_code_str, reason = parts
                status_code = int(status_code_str)
            elif len(parts) == 2:
                version, status_code_str = parts
                status_code = int(status_code_str)
                reason = ""
            else:
                raise ValueError(f"Invalid status line: {status_line}")

            # Parse headers
            headers = {}
            for line in lines[1:]:
                line = line.strip()
                if ": " in line:
                    key, value = line.split(": ", 1)
                    headers[key] = value

            # Set status and headers BEFORE preparing the response
            self.response.set_status(status_code, reason.strip())
            for key, value in headers.items():
                # Skip Content-Length as aiohttp will handle it automatically
                # for streaming responses
                if key.lower() != 'content-length':
                    self.response.headers[key] = value

            # Set session cookie if we have a session ID from tool call
            if self.session_id_from_tool_call:
                self.response.set_cookie(
                    "session_id",
                    self.session_id_from_tool_call,
                    httponly=True,
                    secure=False,  # Set to True in production with HTTPS
                    samesite="Lax"
                )
                app_logger.info(
                    "Session cookie set on response",
                    extra={
                        "session_id": self.session_id_from_tool_call,
                        "client_address": self.client_address_str,
                    }
                )

            # Now prepare the response
            await self.response.prepare(self.request)

            # Write initial body chunk if available
            if initial_body_chunk:
                await self.response.write(initial_body_chunk.encode("utf-8"))

        except (ClientConnectionResetError, ConnectionResetError):
            app_logger.warning(
                "Client disconnected while writing initial body part",
                extra={"client_address": self.client_address_str},
            )
        except Exception as e:
            app_logger.exception(
                "[%s] Error parsing LLM response headers: %s",
                self.client_address_str,
                e,
            )
            # Mark as error so we can potentially send a fallback
            self.model_error_indicator_for_recording = "header_parsing_error"
            # Create a new response object for error case
            self.response = web.StreamResponse(
                status=500, reason="LLM Header Parsing Error"
            )
            await self.response.prepare(self.request)

    @property
    def prepared(self) -> bool:
        return self.response.prepared


class LLMResponseStreamer:
    """
    A streaming response handler that processes LLM output and converts it to
    HTTP responses.
    """

    def __init__(self, client_address_str: str):
        self.client_address_str = client_address_str

    async def stream_response(
        self,
        request: web.Request,
        agent_stream: AsyncGenerator[Any, None],
        max_turns: int,
        session_id: str | None,
    ) -> tuple[web.StreamResponse, str | None, dict[str, Any]]:
        """
        Process the agent stream and return the response with metrics.

        Returns:
            A tuple of (response, final_session_id_for_turn, metrics)
        """
        # Get the agent from the request
        agent = request.app["agent"]

        # Create streaming context
        context = StreamingContext(request, agent)

        # Stream the response
        response, metrics = await context.stream_agent_response(agent_stream)

        # Add timing metrics
        metrics["llm_first_token_time"] = context.llm_first_token_time
        metrics["llm_stream_end_time"] = context.llm_stream_end_time

        final_session_id_for_turn = context.session_id_from_tool_call or session_id

        return response, final_session_id_for_turn, metrics
