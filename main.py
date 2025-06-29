import asyncio
import sys

from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel, set_tracing_disabled
from agents.mcp import MCPServerStdio

from src.config import Config
from src.logging_config import configure_logging, get_loggers


async def main():
    """
    Main entry point for the application.

    This function loads the configuration, starts the core services MCP server,
    and runs the orchestrator agent to set up the application.
    """
    config = Config()
    configure_logging(config.log_level)
    app_logger, _, _ = get_loggers()

    app_logger.info("Starting core-services subprocess...")

    # Connect to the subprocess using the MCP stdio client, which will manage the process
    mcp_client = MCPServerStdio(
        params={
            "command": sys.executable,
            "args": ["src/server/core_services.py"],
            "process_name": "core-services",
        }
    )
    await mcp_client.__aenter__()

    app_logger.info("Core-services MCP client connected.")

    # Create model, using a custom client if a base_url is provided
    if config.openai_base_url:
        custom_client = AsyncOpenAI(
            api_key=config.api_key, base_url=config.openai_base_url
        )
        model = OpenAIChatCompletionsModel(
            model=config.openai_model_name, openai_client=custom_client
        )
        set_tracing_disabled(disabled=True)
        app_logger.info(
            f"Using custom OpenAI client with base_url: {config.openai_base_url}"
        )
        app_logger.info("Tracing disabled for custom provider")
    else:
        model = config.openai_model_name

    orchestrator_agent = Agent(
        name="OrchestratorAgent",
        model=model,
        mcp_servers=[mcp_client],
    )

    app_logger.info("Orchestrator agent created.")

    try:
        with open("src/prompts/orchestrator.md", "r", encoding="utf-8") as f:
            orchestrator_instructions = f.read()
    except FileNotFoundError:
        app_logger.error("Could not find 'src/prompts/orchestrator.md'. Cannot proceed.")
        return

    app_logger.info("Running orchestrator agent...")

    # Run the orchestrator agent
    run_result = Runner.run_streamed(orchestrator_agent, orchestrator_instructions)
    async for event in run_result.stream_events():
        if event.type == "error":
            app_logger.error(f"An error occurred: {event.error}")
            break
    
    app_logger.info(f"Orchestrator Final Output: {run_result.final_output}")

    if not config.one_shot:
        app_logger.info(
            "Orchestration complete. Server process is running in the background."
        )
        app_logger.info("Use Ctrl+C to exit.")
        try:
            # Wait indefinitely until the user interrupts
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
    else:
        app_logger.info("One-shot orchestration complete.")

    # Cleanup is handled by the mcp_client's context manager
    await mcp_client.__aexit__(None, None, None)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting.")
