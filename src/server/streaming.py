# src/server/streaming.py
import json
import logging
import time
from typing import Optional, Tuple

from agents.items import ToolCallItem
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from aiohttp import web
from openai.types.responses import ResponseFunctionToolCall

from ..logging_config import get_loggers

app_logger, access_logger, conversation_logger = get_loggers()


class LLMResponseStreamer:
    """
    Handles the complex streaming logic for LLM responses, including:
    - Parsing streaming events from the agent
    - Processing tool calls
    - Parsing HTTP headers from LLM response
    - Managing session state changes
    - Collecting response text and usage statistics
    """

    def __init__(self, client_address_str: str):
        self.client_address_str = client_address_str
        self.reset_state()

    def reset_state(self):
        """Reset internal state for a new request."""
        self.llm_first_token_time: Optional[float] = None
        self.llm_stream_end_time: Optional[float] = None
        self.llm_response_fully_collected_text_for_log = ""
        self.model_error_indicator_for_recording: Optional[str] = None
        self._last_chunk_finish_reason: Optional[str] = None
        self.prompt_tokens_from_usage = 0
        self.completion_tokens_from_usage = 0
        self.headers_and_status_parsed = False
        self.body_buffer = ""

    async def stream_response(
        self,
        request: web.Request,
        agent_stream,
        max_turns: int,
        initial_session_id: Optional[str] = None,
    ) -> Tuple[web.StreamResponse, Optional[str], dict]:
        """
        Process the LLM agent stream and return the HTTP response.

        Args:
            request: The aiohttp request object
            agent_stream: The streaming agent response
            max_turns: Maximum conversation turns
            initial_session_id: Initial session ID from cookie

        Returns:
            Tuple of (response, final_session_id, metrics_dict)
        """
        self.reset_state()

        response = web.StreamResponse()
        response.enable_chunked_encoding(chunk_size=None)
        final_session_id_for_turn = initial_session_id

        self.llm_first_token_time = time.perf_counter()

        # The core of this server: stream the agent's response.
        # This loop processes events from the agent, including tool calls and content chunks.
        # A key design element is that the LLM is expected to generate a raw HTTP response,
        # which this server parses on-the-fly to construct a valid `aiohttp.web.Response`.
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
                        f"[{self.client_address_str}] Usage found in stream chunk: {usage}"
                    )
                    self.prompt_tokens_from_usage += usage.input_tokens
                    self.completion_tokens_from_usage += usage.output_tokens

            if isinstance(event, RunItemStreamEvent):
                # Only log meaningful stream events at DEBUG level
                if event.name in [
                    "tool_called",
                    "tool_output",
                    "message_output_created",
                ]:
                    app_logger.debug(
                        f"[{self.client_address_str}] Stream event: {event.name}",
                        extra={"event_type": type(event.item).__name__},
                    )
                elif event.name in [
                    "message_chunk_created"
                ] and app_logger.isEnabledFor(logging.DEBUG):
                    # For message chunks, show content preview at DEBUG level
                    chunk_preview = ""
                    if hasattr(event.item, "chunk") and hasattr(
                        event.item.chunk, "text"
                    ):
                        chunk_preview = event.item.chunk.text[:50] + (
                            "..." if len(event.item.chunk.text) > 50 else ""
                        )
                    app_logger.debug(
                        f"[{self.client_address_str}] Received text chunk",
                        extra={
                            "content_preview": chunk_preview,
                            "chunk_length": len(event.item.chunk.text)
                            if hasattr(event.item, "chunk")
                            and hasattr(event.item.chunk, "text")
                            else 0,
                        },
                    )

                if event.name == "tool_called":
                    final_session_id_for_turn = await self._handle_tool_call(
                        event, final_session_id_for_turn
                    )

                if event.name in [
                    "message_chunk_created",
                    "message_output_created",
                ]:
                    chunk = self._extract_chunk_text(event.item)
                    if chunk:
                        await self._process_content_chunk(chunk, response, request)

            else:
                # For RawResponsesStreamEvent and other events, only log if they contain useful info
                if isinstance(
                    event, RawResponsesStreamEvent
                ) and app_logger.isEnabledFor(logging.DEBUG):
                    # Extract useful information from raw response events
                    if hasattr(event, "item") and hasattr(event.item, "raw_item"):
                        # Look for finish reason, usage info, etc.
                        raw_item = event.item.raw_item
                        debug_info = {}
                        if (
                            hasattr(raw_item, "finish_reason")
                            and raw_item.finish_reason
                        ):
                            debug_info["finish_reason"] = raw_item.finish_reason
                        if hasattr(raw_item, "usage") and raw_item.usage:
                            debug_info["tokens"] = (
                                f"input:{raw_item.usage.input_tokens},output:{raw_item.usage.output_tokens}"
                            )

                        if debug_info:  # Only log if we have useful info
                            app_logger.debug(
                                f"[{self.client_address_str}] Raw response event with metadata",
                                extra=debug_info,
                            )
                elif not isinstance(event, RawResponsesStreamEvent):
                    # Log other event types that might be interesting
                    app_logger.debug(
                        f"[{self.client_address_str}] Stream event",
                        extra={"event_type": type(event).__name__},
                    )

        self.llm_stream_end_time = time.perf_counter()

        # If the stream ends and we have a buffer that looks like a response
        # but without the final separator, we process it as a headers-only response.
        if not self.headers_and_status_parsed and self.body_buffer.strip().startswith(
            "HTTP/"
        ):
            app_logger.info(
                f"[{self.client_address_str}] Stream ended with unparsed headers. "
                "Attempting to parse as headers-only response."
            )
            # Treat the whole buffer as headers, with an empty body
            await self._parse_and_prepare_response(
                self.body_buffer, "", response, request
            )

        # Prepare metrics dictionary
        metrics = {
            "llm_response_fully_collected_text_for_log": self.llm_response_fully_collected_text_for_log,
            "model_error_indicator_for_recording": self.model_error_indicator_for_recording,
            "_last_chunk_finish_reason": self._last_chunk_finish_reason,
            "prompt_tokens_from_usage": self.prompt_tokens_from_usage,
            "completion_tokens_from_usage": self.completion_tokens_from_usage,
            "llm_first_token_time": self.llm_first_token_time,
            "llm_stream_end_time": self.llm_stream_end_time,
        }

        return response, final_session_id_for_turn, metrics

    async def _handle_tool_call(
        self, event: RunItemStreamEvent, current_session_id: Optional[str]
    ) -> Optional[str]:
        """Handle tool call events, particularly session ID assignment."""
        # Type-safe check for tool calls
        if isinstance(event.item, ToolCallItem) and isinstance(
            event.item.raw_item, ResponseFunctionToolCall
        ):
            tool_call = event.item.raw_item
            tool_name = tool_call.name
            tool_args = {}
            if tool_call.arguments:
                try:
                    tool_args = json.loads(tool_call.arguments)
                except json.JSONDecodeError:
                    app_logger.warning(
                        f"Could not decode tool arguments: {tool_call.arguments}",
                        extra={
                            "tool_name": tool_name,
                            "error": "Skipping tool call with no valid arguments",
                        },
                    )
                    return current_session_id

            # Log tool call with structured information
            app_logger.debug(
                f"[{self.client_address_str}] LLM calling tool: {tool_name}",
                extra={
                    "tool_name": tool_name,
                    "args_count": len(tool_args),
                    "key_args": str(
                        {
                            k: str(v)[:50] + ("..." if len(str(v)) > 50 else "")
                            for k, v in list(tool_args.items())[:3]
                        }
                    )
                    if tool_args
                    else "none",
                },
            )

            if tool_name == "assign_session_id":
                new_id = tool_args.get("session_id")
                if new_id:
                    app_logger.info(
                        f"Session ID '{new_id}' assigned via tool call. Adopting for logging."
                    )
                    return new_id

        return current_session_id

    def _extract_chunk_text(self, item) -> str:
        """Extract text content from a stream item."""
        chunk = ""
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
        return chunk

    async def _parse_and_prepare_response(
        self,
        header_section: str,
        body_part: str,
        response: web.StreamResponse,
        request: web.Request,
    ):
        """Parses the header section and prepares the aiohttp response."""
        self.headers_and_status_parsed = True

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
                    app_logger.warning(f"Invalid status line: {status_line}")
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    llm_headers[key.strip()] = value.strip()

        response.set_status(llm_status_code)
        for k, v in llm_headers.items():
            # Let aiohttp manage chunking and content length.
            # The LLM may incorrectly provide these headers.
            if k.lower() not in ("content-length", "transfer-encoding"):
                response.headers[k] = v
        await response.prepare(request)

        app_logger.debug(
            f"[{self.client_address_str}] Parsed HTTP response headers from LLM",
            extra={
                "status_code": llm_status_code,
                "headers_count": len(llm_headers),
                "content_type": llm_headers.get("Content-Type", "unknown"),
                "body_preview": body_part[:100]
                + ("..." if len(body_part) > 100 else "")
                if body_part
                else "none",
            },
        )
        app_logger.info(
            f"[{self.client_address_str}] Parsed HTTP headers from LLM, streaming response."
        )

        if body_part:
            await response.write(body_part.encode("utf-8"))

    async def _process_content_chunk(
        self, chunk: str, response: web.StreamResponse, request: web.Request
    ):
        """Process a content chunk, handling header parsing if needed."""
        self.llm_response_fully_collected_text_for_log += chunk

        if self.headers_and_status_parsed:
            await response.write(chunk.encode("utf-8"))
            return

        self.body_buffer += chunk

        # The LLM is expected to stream a full HTTP response, headers and body.
        # We buffer the initial part of the stream until we find the double newline
        # that separates headers from the body.
        separator = None
        if "\r\n\r\n" in self.body_buffer:
            separator = "\r\n\r\n"
        elif "\n\n" in self.body_buffer:
            separator = "\n\n"

        if separator:
            header_section, body_part = self.body_buffer.split(separator, 1)
            await self._parse_and_prepare_response(
                header_section, body_part, response, request
            )
