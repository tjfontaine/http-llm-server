import asyncio
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager

from agents import (
    Agent,
    Runner,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)
from agents.mcp import MCPServerStdio

from src.config import Config
from src.logging_config import configure_logging

app_logger = logging.getLogger("main")


async def wait_for_server(base_url: str, timeout: int = 10) -> bool:
    """Wait for a server to become available using the health check endpoint."""
    import aiohttp

    # Use dedicated health check endpoint for faster, lightweight checks
    health_check_url = f"{base_url}/_health_check"
    checks = timeout // 2
    app_logger.info(
        f"Waiting for server readiness using {health_check_url}, will check {checks} times every 2 seconds..."
    )

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

    app_logger.info(f"Starting application with config: {config}")
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
            )
        except FileNotFoundError:
            app_logger.error(f"Orchestrator file not found: {orchestrator_file}")
            return

        app_logger.info("Running orchestrator agent...")

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
                            f"Tool result: {item.content[:200]}{'...' if len(str(item.content)) > 200 else ''}"
                        )

            if event.type == "final_output":
                app_logger.info("Orchestrator agent completed")
                break

        # If one-shot mode, check server readiness then make one test request
        if config.one_shot:
            app_logger.info(
                "One-shot mode: Checking server readiness then making single test request..."
            )

            # Simple delay to allow server to start up
            app_logger.debug("Waiting 3 seconds for web server to start...")
            await asyncio.sleep(3)

            # Check server readiness using health check endpoint
            base_url = f"http://localhost:{config.port}"
            server_ready = await wait_for_server(base_url, timeout=20)

            if not server_ready:
                app_logger.error(f"Server at {base_url} never became ready")
                return

            try:
                app_logger.info(
                    f"Server is ready! Making single test request to {base_url}/"
                )

                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{base_url}/", timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        response_text = await response.text()

                print("\n" + "=" * 50)
                print("ONE-SHOT HTTP RESPONSE:")
                print("=" * 50)
                print(f"Status: {response.status}")
                print(f"Headers: {dict(response.headers)}")
                print("\nBody:")
                print(response_text)
                print("=" * 50)

                app_logger.info("One-shot request completed successfully")

            except Exception as e:
                app_logger.error(f"One-shot HTTP request failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
