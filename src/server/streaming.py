# src/server/streaming.py
import asyncio
import json
from typing import Any, AsyncGenerator

from agents import Agent
from agents.stream_events import RunItemStreamEvent, RawResponsesStreamEvent
from agents.items import RunItem, ToolCallItem
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
                from openai.types.responses import ResponseTextDeltaEvent, ResponseCompletedEvent
                from openai.types.responses.response_created_event import ResponseCreatedEvent
                
                if isinstance(event.data, ResponseTextDeltaEvent):
                    # Handle text delta events (streaming content)
                    if event.data.delta:
                        await self.process_chunk(event.data.delta)
                elif isinstance(event.data, ResponseCompletedEvent):
                    # Handle completed response events (final response with usage info)
                    if event.data.response.usage:
                        usage = event.data.response.usage
                        app_logger.info(
                            "[%s] Usage in completed response: %s",
                            self.client_address_str,
                            usage,
                        )
                        self.prompt_tokens_from_usage += usage.input_tokens or 0
                        self.completion_tokens_from_usage += usage.output_tokens or 0
                        self.total_tokens_from_usage += usage.total_tokens or 0
                        
                        # Get finish reason from the first choice if available
                        if event.data.response.output and len(event.data.response.output) > 0:
                            first_output = event.data.response.output[0]
                            if hasattr(first_output, 'finish_reason'):
                                self._last_chunk_finish_reason = first_output.finish_reason
                    
                    # For completed responses, we also need to process the full response content
                    # In case it wasn't streamed in deltas
                    if event.data.response.output and len(event.data.response.output) > 0:
                        first_output = event.data.response.output[0]
                        if hasattr(first_output, 'content') and first_output.content:
                            for content_item in first_output.content:
                                if hasattr(content_item, 'text') and content_item.text:
                                    # Only process if we haven't received this content via deltas
                                    if not self.llm_response_fully_collected_text_for_log:
                                        await self.process_chunk(content_item.text)
                elif isinstance(event.data, ResponseCreatedEvent):
                    # Handle response created events (initial response setup)
                    app_logger.debug(
                        "[%s] Response created: %s",
                        self.client_address_str,
                        event.data.response.id,
                    )
                # Handle other event types as needed
                else:
                    app_logger.debug(
                        "[%s] Unhandled raw response event type: %s",
                        self.client_address_str,
                        type(event.data).__name__,
                    )
            elif isinstance(event, RunItemStreamEvent):
                if event.name == "tool_called":
                    await self.handle_tool_calls(event.item)

        # After the stream finishes, handle any remaining buffered content
        if not self.response.prepared:
            app_logger.warning(
                "[%s] Stream finished but HTTP response headers were not finalized.",
                self.client_address_str,
            )

            # Log the collected text for debugging purposes
            log_text = self.llm_response_fully_collected_text_for_log
            log_snippet = f"{log_text[:200]}{'...' if len(log_text) > 200 else ''}"
            app_logger.debug(
                "[%s] Full text collected before parsing headers. Snippet: '%s'",
                self.client_address_str,
                log_snippet,
            )

            # One last attempt to parse, in case headers arrived but no separator
            if (
                self.separator not in self.body_buffer
                and self.body_buffer
                and not self.header_section
            ):
                app_logger.debug(
                    "[%s] Attempting to parse unparsed headers as headers-only "
                    "response",
                    self.client_address_str,
                )
                # Treat the whole buffer as headers, with an empty body
                await self._parse_and_prepare_response(self.body_buffer, "")
                self.body_buffer = ""  # Clear buffer as it's now processed

        if not self.response.prepared:
            app_logger.error(
                "[%s] Stream ended without valid HTTP headers. Buffer: '%.500s%s'",
                self.client_address_str,
                self.body_buffer,
                "..." if len(self.body_buffer) > 500 else "",
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
            "[%s] Handling tool calls: %s",
            self.client_address_str,
            item,
        )
        if isinstance(item, ToolCallItem):
            raw_tool_call = item.raw_item
            if hasattr(raw_tool_call, "function"):
                if raw_tool_call.function:
                    if raw_tool_call.function.name == "assign_session_id":
                        args = json.loads(raw_tool_call.function.arguments)
                        new_id = args.get("session_id")
                        if new_id:
                            app_logger.info(
                                "Session ID '%s' assigned via tool call. "
                                "Adopting for logging.",
                                new_id,
                            )
                            return new_id
        return None

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
                elif '\n\n' in self.body_buffer:
                    separator_found = '\n\n'
                
                if separator_found:
                    header_section, self.body_buffer = self.body_buffer.split(
                        separator_found, 1
                    )
                    self.header_section = header_section
                    app_logger.info(
                        "[%s] Parsing HTTP headers from LLM. Header: '%.200s%s'",
                        self.client_address_str,
                        header_section,
                        "..." if len(header_section) > 200 else "",
                    )
                    await self._parse_and_prepare_response(
                        header_section, self.body_buffer
                    )
                else:
                    app_logger.debug(
                        "[%s] No header separator found yet, continuing to buffer",
                        self.client_address_str,
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
            if '\r\n' in header_section:
                lines = header_section.split('\r\n')
                separator_used = '\r\n'
            else:
                lines = header_section.split('\n')
                separator_used = '\n'
            
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
                self.response.headers[key] = value
            
            # Now prepare the response
            await self.response.prepare(self.request)

            # Write initial body chunk if available
            if initial_body_chunk:
                await self.response.write(initial_body_chunk.encode("utf-8"))
                
        except (ClientConnectionResetError, ConnectionResetError):
            app_logger.warning(
                "[%s] Client disconnected while writing initial body part.",
                self.client_address_str,
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
    A streaming response handler that processes LLM output and converts it to HTTP responses.
    """

    def __init__(self, client_address_str: str):
        self.client_address_str = client_address_str

    async def stream_response(
        self,
        request: web.Request,
        agent_stream: AsyncGenerator[Any, None],
        max_turns: int,
        session_id_from_cookie: str | None,
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
        
        # For now, return the original session_id_from_cookie as final_session_id_for_turn
        # This can be enhanced later to handle session ID changes from tool calls
        final_session_id_for_turn = session_id_from_cookie
        
        return response, final_session_id_for_turn, metrics
