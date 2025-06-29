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
    log_level_upper = log_level.upper()

    # 1. Determine log levels for app and dependencies
    if log_level_upper == "TRACE":
        app_log_level = logging.DEBUG
        deps_log_level = logging.DEBUG
        actual_level_name = "TRACE"
    elif log_level_upper == "DEBUG":
        app_log_level = logging.DEBUG
        deps_log_level = logging.INFO
        actual_level_name = "DEBUG"
    elif log_level_upper == "INFO":
        app_log_level = logging.INFO
        deps_log_level = logging.WARNING  # Default for deps is WARNING
        actual_level_name = "INFO"
    else:
        app_log_level = getattr(logging, log_level_upper, logging.INFO)
        deps_log_level = logging.WARNING
        actual_level_name = log_level_upper

    # 2. Clear all handlers and reset propagation to default
    root_logger = logging.getLogger()
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()
            logger.propagate = True
    root_logger.handlers.clear()

    # 3. Configure the root logger
    # The root logger must be set to the most verbose level of all loggers.
    root_logger.setLevel(min(app_log_level, deps_log_level))
    rich_handler = RichHandler(
        rich_tracebacks=True, show_path=False, show_time=False, markup=True
    )
    rich_handler.setFormatter(EnhancedStructuredFormatter())
    root_logger.addHandler(rich_handler)

    # 4. Set levels for our application-specific loggers
    # They will propagate to the root handler.
    app_logger, access_logger, conversation_logger = get_loggers()
    for logger_instance in [app_logger, access_logger, conversation_logger]:
        logger_instance.setLevel(app_log_level)

    # 5. Set specific log levels for noisy third-party libraries
    # This overrides the root logger's level for these specific logger hierarchies.
    noisy_deps = ["urllib3", "httpcore", "httpx", "aiohttp", "openai", "agents", "mcp"]
    for dep_name in noisy_deps:
        logging.getLogger(dep_name).setLevel(deps_log_level)

    # Get the main app logger for status messages
    app_logger.info(
        f"Logging configured. App level: {logging.getLevelName(app_log_level)}, "
        f"Dependency level: {logging.getLevelName(deps_log_level)}"
    )
    if actual_level_name == "TRACE":
        app_logger.info("TRACE mode: All dependencies are at DEBUG level.")
    elif actual_level_name == "DEBUG":
        app_logger.info("DEBUG mode: Application logs at DEBUG, dependencies at INFO.")

    return app_logger, access_logger, conversation_logger
