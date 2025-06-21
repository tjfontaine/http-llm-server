import logging
from datetime import datetime, timezone

from rich.logging import RichHandler


def get_loggers():
    """Returns the standard loggers for the application."""
    return (
        logging.getLogger("llm_http_server_app"),
        logging.getLogger("http_access"),
        logging.getLogger("conversation_history"),
    )


# Define standard keys to separate them from user-provided 'extra' fields
_STANDARD_LOG_RECORD_KEYS = set(
    logging.LogRecord(
        "dummy", logging.INFO, "dummy.py", 0, "dummy", (), None
    ).__dict__.keys()
)


# Custom formatter for enhanced structured logging
class EnhancedStructuredFormatter(logging.Formatter):
    """Enhanced formatter for consistent, structured logging with rich information."""

    def format(self, record):
        """Formats the log record to include structured extra fields."""
        # Add structured fields to the record
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        level_name = record.levelname
        logger_name = record.name

        # Extract extra fields passed to the logger
        extra_items = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STANDARD_LOG_RECORD_KEYS
        }

        # Create a structured log message
        if extra_items:
            extra_info = " ".join(f"{k}={v}" for k, v in extra_items.items())
            message = f"[{timestamp}] {level_name:8} {logger_name:20} | {record.getMessage()} | {extra_info}"
        else:
            message = (
                f"[{timestamp}] {level_name:8} {logger_name:20} | {record.getMessage()}"
            )

        # Add exception info if present
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message += f"\n{record.exc_text}"

        return message


def configure_logging(log_level: str = "INFO"):
    """Configure all loggers with the specified log level and consistent formatting."""
    # Handle special TRACE level
    if log_level.upper() == "TRACE":
        # TRACE means DEBUG for our app, but also DEBUG for all dependencies
        app_log_level = logging.DEBUG
        deps_log_level = logging.DEBUG
        actual_level_name = "TRACE"
    else:
        # Convert string log level to logging constant
        app_log_level = getattr(logging, log_level.upper(), logging.INFO)
        deps_log_level = None  # Will be set conditionally below
        actual_level_name = log_level.upper()

    # Configure root logger to control all dependencies
    root_logger = logging.getLogger()
    root_logger.setLevel(app_log_level)

    # Get our application loggers
    app_logger, access_logger, conversation_logger = get_loggers()

    # Clear existing handlers
    for logger_instance in [
        root_logger,
        app_logger,
        access_logger,
        conversation_logger,
    ]:
        if logger_instance.hasHandlers():
            logger_instance.handlers.clear()

    # Create enhanced handler with Rich for better console output
    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        show_time=False,  # We include timestamp in our custom formatter
        markup=True,
        level=app_log_level,
    )
    rich_handler.setFormatter(EnhancedStructuredFormatter())

    # Configure root logger (affects all dependencies)
    root_logger.addHandler(rich_handler)
    root_logger.setLevel(app_log_level)

    # Configure our application loggers
    for logger_instance in [app_logger, access_logger, conversation_logger]:
        logger_instance.setLevel(app_log_level)
        logger_instance.propagate = True  # Let them propagate to root logger

    # Configure specific noisy libraries
    if deps_log_level == logging.DEBUG:
        # TRACE mode - show everything from dependencies
        logging.getLogger("urllib3").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("aiohttp").setLevel(logging.DEBUG)
        logging.getLogger("openai").setLevel(logging.DEBUG)
        logging.getLogger("agents").setLevel(logging.DEBUG)
    else:
        # Normal mode (including DEBUG) - suppress noisy dependencies
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("agents").setLevel(
            logging.INFO
        )  # Keep agents library at INFO

    app_logger.info(
        f"Logging configured at {actual_level_name} level for all components"
    )
    if actual_level_name == "TRACE":
        app_logger.info("TRACE mode enabled - showing DEBUG level for all dependencies")

    return app_logger, access_logger, conversation_logger
