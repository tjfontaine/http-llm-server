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
import argparse
import asyncio
import http.cookies
import http.server
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import formatdate

import openai
from aiohttp import web
from pythonjsonlogger import jsonlogger
from rich.logging import RichHandler


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
    async def save_all_sessions_on_shutdown(self, log_directory: str) -> None:
        """Save all current session histories, typically on server shutdown."""
        pass


class InMemorySessionStore(AbstractSessionStore):
    """In-memory implementation of the session store."""

    def __init__(self, save_to_disk: bool = True):
        self._histories: dict[str, list[dict]] = {}
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
LLM_HTTP_SERVER_PROMPT_BASE = """You are an LLM powering an HTTP server. Your primary function is to generate complete and valid HTTP responses.

**Core Task:**
Each time you are invoked, you will receive the raw text of an incoming HTTP request, potentially as part of an ongoing conversation history.
You MUST respond with the complete, raw text of an HTTP response. Your response MUST start *directly* with the HTTP status line (e.g., "HTTP/1.1 200 OK") and nothing else before it. Do NOT use markdown, code fences (like ```http), or any other formatting around your raw HTTP response.

**HTTP Response Structure:**
Your output MUST be the raw HTTP response itself, starting *immediately* with the status line. There should be no other text or explanation before or after the raw HTTP response.
1.  A status line (e.g., "HTTP/1.1 200 OK"). This MUST be the very first line of your output.
2.  One or more header lines (e.g., "Content-Type: text/html; charset=utf-8"). Each header line must be in 'key: value' format.
3.  A single blank line (CRLF, i.e., "\r\n", or LF, i.e., "\n"). This blank line is ABSOLUTELY CRITICAL and MUST be present to separate headers from the body.
4.  The HTTP message body (if any).

**Important Server Behavior Notes for You (LLM):**
-   **No Content-Length:** You MUST NOT calculate or include the `Content-Length` header in your responses. The server will handle appropriate framing for streaming the output (e.g., via `Connection: close` or chunked encoding).
-   **Connection Management:** Do NOT include the `Connection` header (e.g., `Connection: keep-alive`). The server will manage the connection and will close it after sending your response to signal the end of the stream.
-   **Date and Server Headers:** The actual server will add its own `Date` and `Server` headers. For your information, these headers will be similar to `Date: {dynamic_date_example}` and `Server: {dynamic_server_name_example}`. You MUST NOT include `Date` or `Server` headers in YOUR generated response block.
-   **Clean Output:** The response body should consist *only* of the content intended for the client (e.g., HTML, JSON, text). Do not include any extraneous metadata, annotations, internal thoughts.

**Session Management:**
-   Your goal is to maintain a coherent session with each user.
-   **Session ID Injection:** The server will ALWAYS ensure a session ID is present. If the incoming request lacks an "X-Chat-Session-ID" cookie, the server generates one and you MUST include it in your response.
-   **Session Context:** You will receive dynamic session information in your system prompt:
    -   `SESSION_ID`: The current session identifier
    -   `IS_NEW_SESSION`: Boolean indicating if this is a new session (true) or continuing session (false)
    -   `SESSION_HISTORY_COUNT`: Number of previous turns in this session
-   **Cookie Handling Rules:**
    -   **If IS_NEW_SESSION is true**: You MUST include a "Set-Cookie" header in your response:
        `Set-Cookie: X-Chat-Session-ID={{SESSION_ID}}; Path=/; HttpOnly; SameSite=Lax`
    -   **If IS_NEW_SESSION is false**: Do NOT include any "Set-Cookie" header for the session ID (it's already established)

**HTML Generation Requirement:**
-   You are responsible for generating the complete HTML document for each response, including `<!DOCTYPE html>`, `<html>`, `<head>` (with `<meta charset="UTF-8">` and `<meta name="viewport" content="width=device-width, initial-scale=1.0">`), and `<body>` tags.

**Current Session Context:**
- SESSION_ID: {{session_id}}
- IS_NEW_SESSION: {{is_new_session}}
- SESSION_HISTORY_COUNT: {{session_history_count}}
"""

DEFAULT_WEB_APP_TECHNICAL_RULES = """**Default Informational Web Application**

**Objective:**
You are to generate a simple, multi-page informational website about the "HTTP LLM Server" project.
The website should have a homepage and a few other distinct pages.
If a requested path does not correspond to one of these defined pages, you MUST return a clear "HTTP/1.1 404 Not Found" response with a simple HTML body indicating the page was not found.

**Session-Aware Behavior:**
-   **New Sessions (IS_NEW_SESSION = true):** Display a brief welcome message or introduction on the homepage. You may also show a "first visit" indicator.
-   **Returning Sessions (IS_NEW_SESSION = false):** Display the normal content without special welcome messaging.
-   **Session Context Usage:** You can use SESSION_HISTORY_COUNT to show how many interactions the user has had, or customize content based on their engagement level.

**Core Content & Pages:**
1.  **Homepage (Path: `/`):**
    *   **Title:** "Welcome to the HTTP LLM Server Project"
    *   **Content:**
        *   A brief introduction explaining what the HTTP LLM Server is (an AI-powered server that dynamically generates HTTP responses, including web pages, based on LLM interactions).
        *   Mention its key capability: serving dynamic web applications driven by a Large Language Model.
        *   Provide simple navigation links to the other pages (e.g., "About", "Features", "Usage").
        *   **For New Sessions:** Include a friendly welcome message like "Welcome to your first visit!" or "New to the HTTP LLM Server? Start here!"
        *   **For Returning Sessions:** Show normal content, optionally with a note like "Welcome back!" or display interaction count.
    *   A small, visually appealing footer with "Powered by LLM" or similar.

2.  **Random Pages (LLM Discretion):**
    *   You should be prepared to serve content for **three additional, distinct informational pages**.
    *   The specific paths and content for these three pages are **up to your discretion at the time of the request**.
    *   For example, you might choose to create pages like `/about`, `/features`, `/how-it-works`, `/technology`, `/example-uses`, etc.
    *   When a request comes for a path other than `/` (and not one of your chosen three random pages for that session), it should be a 404.
    *   **Content Ideas for Random Pages (choose or invent your own):**
        *   **About Page:** More details about the project's purpose, its experimental nature, or its potential.
        *   **Features Page:** Highlight key features (e.g., dynamic HTML generation, session management, configurable prompts).
        *   **Technology Page:** Briefly mention the core technologies used (e.g., Python, aiohttp, OpenAI LLMs).
        *   **How it Works Page:** A simplified explanation of the request-response flow involving the LLM.
        *   **Usage/Examples Page:** Conceptual ideas on how one might use such a server.
    *   Each of these pages should also have:
        *   A clear title.
        *   A link back to the Homepage.
        *   The same footer as the homepage.

3.  **404 Not Found Page (Any other path):**
    *   **Status Line:** `HTTP/1.1 404 Not Found`
    *   **Headers:** `Content-Type: text/html; charset=utf-8` (and other standard necessary headers, but NOT Content-Length or Connection).
    *   **Body:**
        ```html
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>404 Not Found</title>
            <style>
                body { font-family: sans-serif; text-align: center; padding-top: 50px; color: #333; }
                h1 { font-size: 3em; color: #d9534f; }
                p { font-size: 1.2em; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <h1>404 - Page Not Found</h1>
            <p>Sorry, the page you are looking for does not exist.</p>
            <p><a href="/">Return to Homepage</a></p>
        </body>
        </html>
        ```

**Key Technical Requirements and Guidelines for Generated Web Content (applies to all pages unless it's a 404 response):**
-   **No External Resources:** DO NOT use external resources. All styling via embedded `<style>` tags in `<head>`, all JS inline in `<script>` tags.
-   **Styling:** Simple, clean, professional. Use CSS in `<style>` tags. Good readability.
-   **Interactivity:** Minimal to none. Focus is on informational content. Navigation via standard `<a>` tags.
-   **Semantic HTML:** Use appropriate HTML5 semantic elements.
-   **Viewport Meta Tag:** Ensure `<meta name="viewport" content="width=device-width, initial-scale=1.0">` is in the `<head>`.
-   **Conciseness for Default App:** For this default informational website, aim for concise and to-the-point content on each page to ensure reasonably fast load times and a good default user experience. Brevity is valued here.
-   **Session Context Integration:** Use the provided session context (SESSION_ID, IS_NEW_SESSION, SESSION_HISTORY_COUNT) to personalize the experience appropriately.

**HTTP Response Structure (for 200 OK pages):**
-   Remember to generate the full HTTP response: status line (e.g., `HTTP/1.1 200 OK`), headers (e.g., `Content-Type: text/html; charset=utf-8`), a blank line, and then the HTML body.
-   Do NOT include `Content-Length` or `Connection` headers.
-   **Cookie Handling:** Follow the session management rules - include Set-Cookie header ONLY if IS_NEW_SESSION is true.

**Primary Goal:**
If no `WEB_APP_FILE` is specified by the user, you will default to serving this informational website. The server will still load `DEFAULT_WEB_APP_TECHNICAL_RULES` (which is this entire block) and then look for `WEB_APP_PROMPT_CONTENT_FROM_FILE`. If the file content is empty or the file is not found, `SYSTEM_PROMPT` will effectively be `LLM_HTTP_SERVER_PROMPT` + this informational website prompt.
"""

# --- In-memory Conversation History Storage (for logging and potential rehydration) ---
# Key: session_id, Value: list of conversation turns (OpenAI format: {"role": "user/assistant", "content": "..."})

# --- Logging Configuration (Global for simplicity, initialized early) ---
# Custom formatter for JSON logs
class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            log_record["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if log_record.get("levelname"):
            log_record["severity"] = log_record["levelname"].upper()
            del log_record["levelname"] # Remove original levelname
        else:
            log_record["severity"] = "INFO" # Default severity
        if not log_record.get("logger"):
            log_record["logger"] = record.name

app_logger = logging.getLogger("llm_http_server_app")
app_logger.setLevel(logging.INFO)
access_logger = logging.getLogger("http_access")
access_logger.setLevel(logging.INFO)
conversation_logger = logging.getLogger("conversation_history")
conversation_logger.setLevel(logging.INFO)

# Use the custom JSON formatter
# The format string for JsonFormatter defines which record attributes to pick for the log output.
# We can add more fields here if needed, e.g. '%(module)s %(funcName)s'
json_formatter = CustomJsonFormatter("%(timestamp)s %(severity)s %(logger)s %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(json_formatter) # Apply JSON formatter

# Clear existing handlers and add the new JSON one
for logger_instance in [app_logger, access_logger, conversation_logger]:
    if logger_instance.hasHandlers():
        logger_instance.handlers.clear()
    # Replace StreamHandler with RichHandler for colorful output
    rich_handler = RichHandler(rich_tracebacks=True, show_path=False) # show_path=False to keep logs cleaner
    logger_instance.addHandler(rich_handler)
    logger_instance.propagate = False # Prevent duplicate logs from root logger


def _initialize_configuration_and_client():
    """
    Parses command-line arguments, resolves configuration with environment variables and defaults,
    loads web app prompt file, constructs the system prompt, and initializes the OpenAI client.
    Returns a dictionary containing all resolved configurations and the client.
    Exits if critical configurations (like API key) are missing.
    """
    DEFAULT_PORT = 8080
    DEFAULT_OPENAI_MODEL_NAME = "gpt-4o"
    DEFAULT_OPENAI_TEMPERATURE = 0.7
    DEFAULT_SERVER_NAME_FOR_PROMPT = "LLMWebServer/0.1"

    parser = argparse.ArgumentParser(description="LLM HTTP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to run the server on (default: {DEFAULT_PORT}, or from PORT env var)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("OPENAI_API_KEY"),
        help="OpenAI API Key (can also be set with OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.environ.get("OPENAI_BASE_URL"),
        help="Optional OpenAI compatible base URL (can also be set with OPENAI_BASE_URL env var)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"OpenAI Model Name (default: {DEFAULT_OPENAI_MODEL_NAME}, or from OPENAI_MODEL_NAME env var)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help=f"OpenAI Temperature (default: {DEFAULT_OPENAI_TEMPERATURE}, or from OPENAI_TEMPERATURE env var)",
    )
    parser.add_argument(
        "--web-app-file",
        type=str,
        default=os.environ.get("WEB_APP_FILE"),
        help="Path to a file containing custom web application instructions (can also be set with WEB_APP_FILE env var)",
    )
    parser.add_argument(
        "--save-conversations",
        action="store_true",
        default=os.environ.get("SAVE_CONVERSATIONS", "").lower() in ("true", "1", "yes"),
        help="Save conversation history to files (can also be set with SAVE_CONVERSATIONS env var)",
    )
    args = parser.parse_args()

    config = {}
    config["PORT"] = (
        args.port
        if args.port is not None
        else int(os.environ.get("PORT", DEFAULT_PORT))
    )
    config["API_KEY"] = args.api_key
    config["OPENAI_BASE_URL"] = args.base_url
    config["OPENAI_MODEL_NAME"] = (
        args.model
        if args.model is not None
        else os.environ.get("OPENAI_MODEL_NAME", DEFAULT_OPENAI_MODEL_NAME)
    )
    config["OPENAI_TEMPERATURE"] = (
        args.temperature
        if args.temperature is not None
        else float(os.environ.get("OPENAI_TEMPERATURE", DEFAULT_OPENAI_TEMPERATURE))
    )
    config["WEB_APP_FILE"] = args.web_app_file
    config["SAVE_CONVERSATIONS"] = args.save_conversations

    if not config["API_KEY"]:
        app_logger.error(
            "OpenAI API Key not provided. Please set OPENAI_API_KEY environment variable or use --api-key."
        )
        exit(1)

    WEB_APP_PROMPT_CONTENT_FROM_FILE = ""
    if config["WEB_APP_FILE"]:
        try:
            with open(config["WEB_APP_FILE"], "r", encoding="utf-8") as f:
                WEB_APP_PROMPT_CONTENT_FROM_FILE = f.read()
            if WEB_APP_PROMPT_CONTENT_FROM_FILE.strip():
                app_logger.info(
                    f"Successfully loaded web app prompt content from: {config['WEB_APP_FILE']}"
                )
            else:
                app_logger.warning(
                    f"Web app prompt file '{config['WEB_APP_FILE']}' is empty. Using default web app technical rules only."
                )
                WEB_APP_PROMPT_CONTENT_FROM_FILE = ""
        except FileNotFoundError:
            app_logger.warning(
                f"Web app prompt file not found: {config['WEB_APP_FILE']}. Using default web app technical rules only."
            )
        except Exception:
            app_logger.exception(
                f"Error reading web app prompt file '{config['WEB_APP_FILE']}':"
            )
            app_logger.warning(
                "Proceeding with default web app technical rules only due to error."
            )
    else:
        app_logger.info(
            "No WEB_APP_FILE specified (via --web-app-file or WEB_APP_FILE env var). Using default web app technical rules only for web app content."
        )

    # Prepare dynamic examples for the LLM server prompt
    server_name_example_for_prompt = DEFAULT_SERVER_NAME_FOR_PROMPT
    current_gmt_date_example_for_prompt = formatdate(timeval=None, localtime=False, usegmt=True)

    formatted_llm_server_prompt = LLM_HTTP_SERVER_PROMPT_BASE.format(
        dynamic_date_example=current_gmt_date_example_for_prompt,
        dynamic_server_name_example=server_name_example_for_prompt
    )

    if WEB_APP_PROMPT_CONTENT_FROM_FILE.strip():
        web_app_rules_section_content = WEB_APP_PROMPT_CONTENT_FROM_FILE.strip()
    else:
        web_app_rules_section_content = DEFAULT_WEB_APP_TECHNICAL_RULES.strip()

    config["SYSTEM_PROMPT"] = f"""{formatted_llm_server_prompt.strip()}

<web_application_rules>
{web_app_rules_section_content}
</web_application_rules>"""

    try:
        client_args = {"api_key": config["API_KEY"]}
        if config["OPENAI_BASE_URL"]:
            client_args["base_url"] = config["OPENAI_BASE_URL"]
        config["openai_client"] = openai.AsyncOpenAI(**client_args)
        app_logger.info(
            f"OpenAI client initialized. Using API Key: {'*' * (len(config['API_KEY']) - 4) + config['API_KEY'][-4:] if config['API_KEY'] and len(config['API_KEY']) > 4 else 'Provided'}. "
            f"Model: {config['OPENAI_MODEL_NAME']}. Base URL: {config['OPENAI_BASE_URL'] if config['OPENAI_BASE_URL'] else 'Default OpenAI'}. Temperature: {config['OPENAI_TEMPERATURE']}"
        )
    except Exception:
        app_logger.exception("Error initializing OpenAI client:")
        app_logger.error(
            "Please ensure your API key and base URL (if provided) are valid and the openai library is installed."
        )
        exit(1)

    return config


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
    """Sends a generic HTML error response if the LLM interaction fails, for aiohttp."""
    error_details_html = (
        error_details.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Server Error: {message}</title><style>body {{font-family: sans-serif; margin:20px;}} h1 {{color: #cc0000;}} .details {{background-color: #f0f0f0; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word;}}</style></head>
<body><h1>HTTP {status_code} - {message}</h1><p class="details">{error_details_html}</p></body>
</html>"""
    return web.Response(
        text=html_body,
        status=status_code,
        content_type="text/html",
        charset="utf-8",
        headers={"Connection": "close"},
    )


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

    current_session_store: AbstractSessionStore = request.app[
        "session_store"
    ]  # Get store from app context

    raw_request_text = await _get_raw_request_aiohttp(request)
    server_generated_session_id_for_history = None  # For conversation_histories key
    final_session_id_for_logging = (
        None  # For access logs, could be server's or LLM's cookie value
    )
    new_session_id_generated_by_server = False

    cookie_header = request.headers.get("Cookie")
    if cookie_header:
        try:
            cookies = http.cookies.SimpleCookie()
            cookies.load(cookie_header)
            if "X-Chat-Session-ID" in cookies:
                existing_session_id_from_cookie = cookies["X-Chat-Session-ID"].value
                if existing_session_id_from_cookie:
                    server_generated_session_id_for_history = (
                        existing_session_id_from_cookie
                    )
                    final_session_id_for_logging = existing_session_id_from_cookie
                    app_logger.info(
                        f"[{client_address_str}] Existing session ID found in cookie: {final_session_id_for_logging}"
                    )
        except Exception:
            app_logger.exception(
                f"[{client_address_str}] Error parsing 'Cookie' header: '{cookie_header}'. Treating as no session ID."
            )

    if not server_generated_session_id_for_history:
        new_uuid = str(uuid.uuid4())
        server_generated_session_id_for_history = new_uuid
        final_session_id_for_logging = (
            new_uuid  # Initially server's, might be updated by LLM's cookie
        )
        new_session_id_generated_by_server = True
        app_logger.info(
            f"[{client_address_str}] No valid session ID in request. Server generated new session ID for history: {server_generated_session_id_for_history}"
        )

    messages = []
    if server_generated_session_id_for_history:
        history = await current_session_store.get_history(
            server_generated_session_id_for_history
        )  # Use new store
        for turn in history:
            messages.append(
                {"role": turn["role"], "content": str(turn.get("content", ""))}
            )
    messages.append({"role": "user", "content": raw_request_text})

    system_prompt = request.app["system_prompt"]
    openai_client = request.app["openai_client"]
    model_name = request.app["openai_model_name"]
    temperature = request.app["openai_temperature"]
    
    # Build dynamic system prompt with session context
    session_history_count = len(messages) - 1  # Subtract 1 for the current user message
    
    # Replace the session context placeholders in the system prompt
    # Note: The initial format() call converts {{session_id}} to {session_id}
    dynamic_system_prompt = system_prompt.replace("{session_id}", server_generated_session_id_for_history)
    dynamic_system_prompt = dynamic_system_prompt.replace("{is_new_session}", str(new_session_id_generated_by_server).lower())
    dynamic_system_prompt = dynamic_system_prompt.replace("{session_history_count}", str(session_history_count))
    
    # Debug logging to see what session context is being passed
    app_logger.info(
        f"[{client_address_str}] Session context: ID={server_generated_session_id_for_history}, "
        f"IsNew={new_session_id_generated_by_server}, HistoryCount={session_history_count}"
    )
    
    # Debug: Show a snippet of the system prompt to verify session context injection
    session_context_snippet = dynamic_system_prompt[dynamic_system_prompt.find("**Current Session Context:**"):dynamic_system_prompt.find("**Current Session Context:**") + 200]
    app_logger.debug(f"[{client_address_str}] System prompt session context snippet: {session_context_snippet}")
    
    full_prompt_messages = [{"role": "system", "content": dynamic_system_prompt}] + messages

    # --- LLM Interaction and Response Handling ---
    llm_call_start_time = None
    llm_first_token_time = None
    llm_stream_end_time = None
    llm_response_fully_collected_text_for_log = ""
    model_error_indicator_for_recording = None
    _last_chunk_finish_reason = None
    prompt_tokens_from_usage = 0
    completion_tokens_from_usage = 0

    try:
        llm_call_start_time = time.perf_counter()
        llm_stream = await openai_client.chat.completions.create(
            model=model_name,
            messages=full_prompt_messages,
            temperature=temperature,
            stream=True,
        )

        # Stream LLM response with header parsing
        llm_buffer = ""
        headers_parsed = False
        llm_status_code = 200
        llm_headers = {}
        response = None
        
        async for chunk in llm_stream:
            # Always check for usage on the chunk object itself and update if present.
            if chunk.usage:
                app_logger.debug(
                    f"[{client_address_str}] Stream usage reported by API (chunk.usage): {chunk.usage}"
                )
                prompt_tokens_from_usage = chunk.usage.prompt_tokens
                completion_tokens_from_usage = chunk.usage.completion_tokens

            if not chunk.choices:
                continue

            delta_content = chunk.choices[0].delta.content
            finish_reason_from_chunk = chunk.choices[0].finish_reason

            if finish_reason_from_chunk:
                _last_chunk_finish_reason = finish_reason_from_chunk

            if delta_content is None:
                if (
                    _last_chunk_finish_reason
                    and not delta_content
                    and not llm_response_fully_collected_text_for_log
                ):
                    # LLM refused to respond
                    app_logger.warning(
                        f"[{client_address_str}] LLM stream ended with finish_reason '{_last_chunk_finish_reason}' before any content was generated."
                    )
                    model_error_indicator_for_recording = f"LLM_REFUSED_TO_RESPOND_FINISH_REASON_{_last_chunk_finish_reason}"
                    llm_response_fully_collected_text_for_log = (
                        f"[LLM_REFUSED_RESPONSE: {_last_chunk_finish_reason}]"
                    )
                    break
                elif not delta_content:
                    continue

            if llm_first_token_time is None and delta_content.strip():
                llm_first_token_time = time.perf_counter()

            llm_response_fully_collected_text_for_log += delta_content

            if not headers_parsed:
                llm_buffer += delta_content
                
                # Look for end of headers (double newline)
                if "\r\n\r\n" in llm_buffer:
                    header_end = llm_buffer.find("\r\n\r\n")
                    headers_section = llm_buffer[:header_end]
                    body_start = llm_buffer[header_end + 4:]
                    headers_parsed = True
                elif "\n\n" in llm_buffer:
                    header_end = llm_buffer.find("\n\n")
                    headers_section = llm_buffer[:header_end]
                    body_start = llm_buffer[header_end + 2:]
                    headers_parsed = True
                
                if headers_parsed:
                    # Parse status line and headers
                    lines = headers_section.split('\n')
                    if lines:
                        # Parse status line (e.g., "HTTP/1.1 200 OK")
                        status_line = lines[0].strip()
                        if status_line.startswith("HTTP/"):
                            try:
                                parts = status_line.split(' ', 2)
                                if len(parts) >= 2:
                                    llm_status_code = int(parts[1])
                            except ValueError:
                                app_logger.warning(f"[{client_address_str}] Invalid status code in LLM response: {status_line}")
                        
                        # Parse headers
                        for line in lines[1:]:
                            line = line.strip()
                            if ':' in line:
                                key, value = line.split(':', 1)
                                llm_headers[key.strip()] = value.strip()
                    
                    # Create StreamResponse with parsed status and headers
                    response = web.StreamResponse(status=llm_status_code)
                    
                    # Set headers from LLM (excluding ones aiohttp manages)
                    for key, value in llm_headers.items():
                        if key.lower() not in ['content-length', 'transfer-encoding', 'connection']:
                            response.headers[key] = value
                    
                    # Prepare the response
                    await response.prepare(request)
                    
                    # Write any body content we already have
                    if body_start:
                        await response.write(body_start.encode('utf-8'))
            else:
                # Headers already parsed, stream body content
                if response:
                    await response.write(delta_content.encode('utf-8'))

        llm_stream_end_time = time.perf_counter()

        # Ensure we have a response object
        if not response:
            # Fallback if no headers were parsed
            response = web.Response(
                text=llm_response_fully_collected_text_for_log,
                content_type='text/plain',
                charset='utf-8'
            )

    except openai.APIConnectionError as e:
        app_logger.exception(f"[{client_address_str}] OpenAI API Connection Error:")
        model_error_indicator_for_recording = "API_CONNECTION_ERROR"
        llm_response_fully_collected_text_for_log = f"ERROR_API_CONNECTION: {e}"
        return await _send_llm_error_response_aiohttp(
            request, 503, "LLM Service Unavailable", f"API Connection Error: {e}"
        )
    except openai.APIStatusError as e:
        app_logger.exception(
            f"[{client_address_str}] OpenAI API Status Error (code {e.status_code}): {e.message}"
        )
        model_error_indicator_for_recording = f"API_STATUS_ERROR_{e.status_code}"
        llm_response_fully_collected_text_for_log = (
            f"ERROR_API_STATUS_{e.status_code}: {e.message}"
        )
        return await _send_llm_error_response_aiohttp(
            request,
            e.status_code if e.status_code else 500,
            "LLM API Error",
            f"API Error: {e.message}",
        )
    except openai.APIError as e:
        app_logger.exception(f"[{client_address_str}] OpenAI API Generic Error:")
        model_error_indicator_for_recording = "API_GENERIC_ERROR"
        llm_response_fully_collected_text_for_log = f"ERROR_API_GENERIC: {e}"
        return await _send_llm_error_response_aiohttp(
            request, 500, "LLM Service Error", f"Generic API Error: {e}"
        )
    except Exception:
        app_logger.exception(
            f"[{client_address_str}] Unexpected error processing LLM stream:"
        )
        model_error_indicator_for_recording = "UNEXPECTED_STREAM_PROCESSING_ERROR"
        llm_response_fully_collected_text_for_log = "ERROR_UNEXPECTED_STREAM_PROCESSING"
        return await _send_llm_error_response_aiohttp(
            request,
            500,
            "Internal Server Error",
            "Unexpected error during stream processing.",
        )
    finally:
        # Record conversation history (always in memory for session context, optionally to disk)
        if server_generated_session_id_for_history:
            await current_session_store.record_turn(
                server_generated_session_id_for_history, "user", raw_request_text
            )

            # Prepare assistant content for session history
            assistant_content_for_history = llm_response_fully_collected_text_for_log
            if model_error_indicator_for_recording:
                assistant_content_for_history = f"[LLM_RESPONSE_STREAM_INTERRUPTED_OR_ERROR: {model_error_indicator_for_recording}]\n\n{llm_response_fully_collected_text_for_log}"
            elif not llm_response_fully_collected_text_for_log.strip():
                assistant_content_for_history = "[LLM_EMPTY_RESPONSE_STREAMED]"

            await current_session_store.record_turn(
                server_generated_session_id_for_history,
                "assistant",
                assistant_content_for_history,
            )
        else:
            app_logger.error(
                f"[{client_address_str}] server_generated_session_id_for_history was not set. Cannot record conversation turns."
            )

        # Calculate timing and token metrics
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

        # Calculate tokens per second
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
                    compl_tokens_per_sec_val = float('inf')
                else:
                    compl_tokens_per_sec_str = "0.00 (no tokens, instantaneous)"
                    compl_tokens_per_sec_val = 0.0

        log_msg_main_part = (
            f"[{client_address_str}] Request handled. "
            f"TotalDur: {duration:.3f}s, LLM_TTFT: {ttft_str}, LLM_StreamDur: {duration_llm_stream_str}, "
            f"PToken: {prompt_tokens_from_usage}, CToken: {completion_tokens_from_usage}, CTPS: {compl_tokens_per_sec_str}, "
            f"Sess: {server_generated_session_id_for_history}/{final_session_id_for_logging}, NewSrvSess: {new_session_id_generated_by_server}, "
            f"FinishReason: {_last_chunk_finish_reason if _last_chunk_finish_reason else 'N/A'}."
        )
        
        access_log_extra = {
            "client_address": client_address_str,
            "total_duration_seconds": round(duration, 3),
            "llm_ttft_seconds": round(llm_ttft_seconds_val, 3) if llm_ttft_seconds_val is not None else None,
            "llm_stream_duration_seconds": round(llm_stream_duration_seconds_val, 3) if llm_stream_duration_seconds_val is not None else None,
            "prompt_tokens": prompt_tokens_from_usage,
            "completion_tokens": completion_tokens_from_usage,
            "completion_tokens_per_second": round(compl_tokens_per_sec_val, 2) if compl_tokens_per_sec_val is not None and compl_tokens_per_sec_val != float('inf') else compl_tokens_per_sec_val,
            "session_hkey": server_generated_session_id_for_history,
            "session_log_id": final_session_id_for_logging,
            "new_session_by_server": new_session_id_generated_by_server,
            "http_method": request.method,
            "http_path_qs": request.path_qs,
            "llm_finish_reason": _last_chunk_finish_reason
        }

        log_msg_final = log_msg_main_part
        if model_error_indicator_for_recording:
            access_log_extra["error_indicator"] = model_error_indicator_for_recording
            access_log_extra['llm_raw_response_on_error'] = llm_response_fully_collected_text_for_log
            log_msg_final += f" Error: {model_error_indicator_for_recording}."

        access_logger.info(log_msg_final, extra=access_log_extra)
        
    return response


async def on_startup(app: web.Application):
    """Async operations to perform on server startup."""
    # If any async init for client or other resources is needed, do it here.
    # For now, client is initialized synchronously in _initialize_configuration_and_client
    # and stored in app context.
    app_logger.info("Server startup actions completed.")


async def on_shutdown(app: web.Application):
    """Async operations to perform on server shutdown."""
    app_logger.info("\nServer shutting down (async)...")

    log_directory = "conversation_logs"
    current_session_store: AbstractSessionStore = app[
        "session_store"
    ]  # Get store from app context
    await current_session_store.save_all_sessions_on_shutdown(
        log_directory
    )  # Use new store (will handle conditional saving internally)

    # Close OpenAI client if it has an async close method (AsyncOpenAI does)
    if app.get("openai_client") and hasattr(app["openai_client"], "close"):
        try:
            await app["openai_client"].close()
            app_logger.info("Async OpenAI client closed.")
        except Exception:
            app_logger.exception("Error closing Async OpenAI client:")

    app_logger.info("Server shutdown actions completed.")


def run_server():
    """Initializes configuration, sets up the aiohttp app, and starts the HTTP server."""
    config = _initialize_configuration_and_client()

    # Initialize session store with configuration
    session_store = InMemorySessionStore(save_to_disk=config["SAVE_CONVERSATIONS"])

    app = web.Application()
    # Store config and client in app context for handler access
    app["system_prompt"] = config["SYSTEM_PROMPT"]
    app["openai_client"] = config["openai_client"]
    app["openai_model_name"] = config["OPENAI_MODEL_NAME"]
    app["openai_temperature"] = config["OPENAI_TEMPERATURE"]
    app["port"] = config["PORT"]
    app["save_conversations"] = config["SAVE_CONVERSATIONS"]
    app["session_store"] = session_store  # Add session_store to app context

    app.router.add_route(
        "*", "/{path:.*}", handle_http_request
    )  # Catch all paths and methods

    # Ensure on_startup and on_shutdown are defined before being appended here
    # These are typically defined at the module level or imported.
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    port_to_use = config["PORT"]
    app_logger.info(
        f"LLM HTTP Server (Async using aiohttp) starting on port {port_to_use}"
    )
    app_logger.info(
        f"Configuration: API Key: {'Set' if config['API_KEY'] else 'NOT SET (REQUIRED!)'}, "
        f"Base URL: {config['OPENAI_BASE_URL'] or 'Default'}, Model: {config['OPENAI_MODEL_NAME']}, Temp: {config['OPENAI_TEMPERATURE']}, "
        f"Web App File: {config['WEB_APP_FILE'] or 'Not set'}, Save Conversations: {config['SAVE_CONVERSATIONS']}"
    )
    app_logger.info(
        f"To override, use command-line arguments (e.g., --port {port_to_use}) or environment variables."
    )
    app_logger.info(f"Access the server at http://localhost:{port_to_use}")

    web.run_app(app, host="0.0.0.0", port=port_to_use, access_log=access_logger)
    # Note: web.run_app handles its own try/except for KeyboardInterrupt for graceful shutdown.
    # The on_shutdown callback will be triggered.


if __name__ == "__main__":
    # http.cookies is used in LLMHTTPRequestHandler, so ensure it's imported.
    # It's already imported globally.
    run_server()
