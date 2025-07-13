import asyncio
import logging
import os
import shutil
import signal
import sys
from contextlib import asynccontextmanager

import jinja2
from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from agents.mcp import MCPServerStdio

from src.config import Config
from src.logging_config import configure_logging

app_logger = logging.getLogger("llm_http_server_app")


async def wait_for_server(base_url: str, timeout: int = 10) -> bool:
    """Wait for a server to become available using the health check endpoint."""
    import aiohttp

    # Use dedicated health check endpoint for faster, lightweight checks
    health_check_url = f"{base_url}/_health_check"
    checks = timeout // 2
    app_logger.info(f"Waiting for server readiness at {health_check_url}...")
    app_logger.info(f"Will check {checks} times every 2 seconds.")

    for i in range(checks):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    health_check_url, timeout=aiohttp.ClientTimeout(total=3)
                ) as response:
                    if response.status == 200:
                        response_text = await response.text()
                        if response_text.strip() == "OK":
                            app_logger.info(f"Server is ready after {i + 1} check(s)")
                            return True
        except Exception as e:
            app_logger.debug(f"Health check {i + 1}/{checks} failed: {e}")

        if i < checks - 1:  # Don't sleep after the last attempt
            await asyncio.sleep(2.0)

    app_logger.warning(f"Server not ready after {checks} health checks")
    return False


@asynccontextmanager
async def core_services_server(log_level: str):
    """Start the core services MCP server subprocess."""
    app_logger.info("Starting core services subprocess...")

    # Pass the log level to the subprocess via environment variable
    env = os.environ.copy()
    env["CORE_SERVICES_LOG_LEVEL"] = log_level

    server = MCPServerStdio(
        params={
            "command": sys.executable,
            "args": ["-m", "src.server.core_services"],
            "env": env,
        },
        client_session_timeout_seconds=30.0,  # Increase timeout from 5 to 30 seconds
    )

    try:
        await server.__aenter__()
        yield server
    finally:
        app_logger.info("Terminating core services subprocess...")
        await server.__aexit__(None, None, None)


async def main():
    """Main entry point for the application."""
    # Create a shutdown event
    shutdown_event = asyncio.Event()

    # Get the current loop
    loop = asyncio.get_running_loop()

    # Set up signal handlers
    def _signal_handler():
        app_logger.info("Shutdown signal received, initiating graceful shutdown.")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Load configuration
    config = Config()

    # Load default system prompt if not already set
    if not config.system_prompt_template:
        try:
            with open("src/prompts/system.md", "r", encoding="utf-8") as f:
                config.system_prompt_template = f.read()
        except FileNotFoundError:
            app_logger.warning(
                "Default system prompt 'src/prompts/system.md' not found."
            )

    configure_logging(config.log_level)
    logging.getLogger("agents").setLevel(logging.DEBUG)

    app_logger.info(
        "Starting application with key configurations",
        extra={
            "one_shot": config.one_shot,
            "log_level": config.log_level,
            "web_app": config.web_app_file,
            "model": config.openai_model_name,
        },
    )
    if config.one_shot:
        app_logger.info("Running in one-shot mode")

    # Check for required executables
    if not shutil.which("uv"):
        app_logger.error("uv is not installed or not in PATH. Please install uv first.")
        return

    # Start core services MCP server only (local tools handled at WebServer level)
    async with core_services_server(config.log_level) as core_services_client:
        # Create the orchestrator agent with only core services
        mcp_servers = [core_services_client]

        # Create the orchestrator agent with proper model configuration
        if config.openai_base_url:
            custom_client = AsyncOpenAI(
                api_key=config.api_key, base_url=config.openai_base_url
            )
            model = OpenAIChatCompletionsModel(
                model=config.openai_model_name, openai_client=custom_client
            )
            set_tracing_disabled(disabled=True)
            app_logger.debug(
                f"Using custom OpenAI client with base_url: {config.openai_base_url}"
            )
        else:
            model = config.openai_model_name

        agent = Agent("orchestrator-agent", model=model, mcp_servers=mcp_servers)

        # Read the orchestrator instructions and inject config values
        orchestrator_file = "src/prompts/orchestrator.md"
        try:
            with open(orchestrator_file, "r") as f:
                orchestrator_template = f.read()

            # Inject configuration values into the template
            orchestrator_instructions = orchestrator_template.format(
                web_app_file=config.web_app_file or "",
                enable_local_tools=str(config.local_tools_enabled).lower(),
                log_level=config.log_level,
            )
        except FileNotFoundError:
            app_logger.error(f"Orchestrator file not found: {orchestrator_file}")
            return

        app_logger.info("Running orchestrator agent...")
        app_logger.debug(f"Orchestrator instructions: {orchestrator_instructions}")

        # Pass the config as context for future extensibility
        result = Runner.run_streamed(agent, orchestrator_instructions, context=config)
        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                if hasattr(event, "item"):
                    item = event.item
                    if hasattr(item, "name") and item.name in [
                        "tool_called",
                        "tool_output",
                    ]:
                        app_logger.debug(f"Agent tool: {item.name}")
                    if hasattr(item, "content") and item.content:
                        app_logger.debug(
                            "Tool result: %.200s%s",
                            item.content,
                            "..." if len(str(item.content)) > 200 else "",
                        )

            if event.type == "final_output":
                app_logger.info("Orchestrator agent completed")
                break

        # If one-shot mode, check server readiness then make N test requests
        if config.one_shot:
            app_logger.info("One-shot mode: Checking server readiness...")
            await asyncio.sleep(2)  # Give the server a moment to be ready
            if not await wait_for_server(
                f"http://localhost:{config.port}", timeout=20
            ):
                app_logger.error("Server failed to start, aborting one-shot tests.")
                return

            import aiohttp

            # Pre-warm the application to initialize database, etc.
            prewarm_url = f"http://localhost:{config.port}/_prewarm"
            app_logger.info(f"Pre-warming application at {prewarm_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        prewarm_url,
                        timeout=aiohttp.ClientTimeout(total=300),
                        allow_redirects=False,
                    ) as response:
                        if response.status == 302:
                            app_logger.info(
                                f"Pre-warm successful with redirect to {response.headers.get('Location')}."
                            )
                        else:
                            response_text = await response.text()
                            app_logger.warning(
                                f"Pre-warm returned status {response.status}, proceeding anyway. "
                                f"Response: {response_text[:200]}"
                            )
            except Exception as e:
                app_logger.error(f"Pre-warm request failed: {e}")

            app_logger.info(
                f"Server is ready! Making {config.one_shot} test request(s) to http://localhost:{config.port}/"
            )

            try:
                async with aiohttp.ClientSession() as session:
                    for i in range(config.one_shot):
                        app_logger.info(
                            f"Making test request {i + 1}/{config.one_shot}..."
                        )
                        async with session.get(
                            f"http://localhost:{config.port}/",
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as response:
                            response_text = await response.text()

                        print("\n" + "=" * 50)
                        print(f"ONE-SHOT HTTP RESPONSE {i + 1}/{config.one_shot}:")
                        print("=" * 50)
                        print(f"Status: {response.status}")
                        print(f"Headers: {dict(response.headers)}")
                        print("\nBody:")
                        print(response_text)
                        print("=" * 50)

                app_logger.info(
                    f"All {config.one_shot} one-shot request(s) completed successfully"
                )

            except Exception as e:
                app_logger.error(f"One-shot HTTP request(s) failed: {e}")
        else:
            app_logger.info("Application started successfully. Running in server mode.")
            # Wait for the shutdown event
            await shutdown_event.wait()
            app_logger.info("Shutdown event received, server is stopping.")


if __name__ == "__main__":
    asyncio.run(main())
