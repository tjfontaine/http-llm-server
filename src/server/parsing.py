"""
Parsing utility functions for the HTTP LLM Server.

This module contains stateless helper functions for parsing web app files
and constructing raw HTTP requests.
"""

import re

import yaml
from aiohttp import web

from ..logging_config import get_loggers

# Get loggers for this module
app_logger, _, _ = get_loggers()

# Default web app file path
DEFAULT_WEB_APP_FILE = "examples/default_info_site/prompt.md"


def parse_webapp_file(file_path):
    """
    Parse a markdown file with YAML front matter.
    Returns a tuple of (yaml_data, markdown_content).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if content.startswith("---\n"):
            match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
            if match:
                yaml_content = match.group(1)
                markdown_content = match.group(2)
                yaml_data = yaml.safe_load(yaml_content) if yaml_content.strip() else {}
                return yaml_data, markdown_content.strip()

            app_logger.warning(f"Invalid YAML front matter format in {file_path}")
            return {}, content

        # No front matter, treat as plain markdown/text
        return {}, content

    except yaml.YAMLError as e:
        app_logger.error(f"YAML parsing error in {file_path}: {e}")
        return {}, ""
    except Exception as e:
        app_logger.error(f"Error reading webapp file {file_path}: {e}")
        return {}, ""


async def get_raw_request_str(request: web.Request) -> str:
    """
    Constructs the raw HTTP request string from an aiohttp.web.Request object.
    """
    raw_request_line_str = (
        f"{request.method} {request.path_qs} "
        f"HTTP/{request.version.major}.{request.version.minor}"
    )
    header_lines = [f"{key}: {value}" for key, value in request.headers.items()]
    body_str = ""
    body_bytes = await request.read()
    if body_bytes:
        charset = request.charset or "utf-8"
        try:
            body_str = body_bytes.decode(charset)
        except (UnicodeDecodeError, LookupError):
            app_logger.warning(
                f"Could not decode request body with charset {charset}, "
                "used latin-1 fallback."
            )
            body_str = body_bytes.decode("latin-1", "replace")

    return "\r\n".join([raw_request_line_str] + header_lines + ["", body_str])
