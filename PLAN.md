# Refactoring Plan: `src/server.py`

This document outlines a step-by-step plan to refactor the monolithic
`src/server.py` file. The primary goals are to improve modularity, readability,
testability, and observability by applying modern software design principles.

The refactoring is broken down into a series of distinct, incremental steps,
each corresponding to a logical git commit.

## Guiding Proposals

1.  **Modularize by Responsibility**: Decompose the single large file into
    smaller, focused modules (e.g., for session management, parsing, etc.).
2.  **Leverage `aiohttp` Middlewares**: Use the web framework's native
    middleware system for cross-cutting concerns like logging, session handling,
    and error catching.
3.  **Abstract the Core LLM Loop**: Encapsulate the complex, stateful
    response-streaming logic into a dedicated, reusable component.
4.  **Introduce Structured Data Models**: Replace generic dictionaries (`dict`)
    and lists with Pydantic models to ensure type safety and make data contracts
    explicit.

---

## The Plan: Step-by-Step

### Step 1: Foundational - Introduce Pydantic Data Models

**Commit:
`refactor(server): Introduce Pydantic models for session and chat data`**

- **Why**: Start with the data structures. They are the foundation upon which
  the logic is built. This makes subsequent steps cleaner and safer.
- **Actions**:
  1.  Create a new directory `src/server/`.
  2.  Create a new file `src/server/models.py`.
  3.  In `models.py`, define Pydantic models like `ChatMessage` and
      `ConversationHistory`.
  4.  Update the `AbstractSessionStore` and `InMemorySessionStore` method
      signatures in `src/server.py` to use these new models instead of `dict`
      and `list[dict]`.

### Step 2: Modularization - Extract Session Management

**Commit: `refactor(server): Move session management to src/server/session.py`**

- **Why**: The session store is a self-contained and critical piece of state
  management. It's a prime candidate for early extraction.
- **Actions**:
  1.  Create `src/server/session.py`.
  2.  Move the `AbstractSessionStore` and `InMemorySessionStore` classes from
      `src/server.py` into `src/server/session.py`.
  3.  Import the new Pydantic models from `src/server/models.py`.
  4.  Update `create_app` in `src/server.py` to import and use
      `InMemorySessionStore` from its new location.

### Step 3: Modularization - Extract Parsing Helpers

**Commit:
`refactor(server): Move request and file parsing logic to src/server/parsing.py`**

- **Why**: Group pure, stateless helper functions into a dedicated utility
  module.
- **Actions**:
  1.  Create `src/server/parsing.py`.
  2.  Move the `_parse_webapp_file` and `_get_raw_request_aiohttp` functions
      into this new file.
  3.  Update the call sites within `src/server.py` to use the imported
      functions.

### Step 4: Modularization - Extract Agent & MCP Setup

**Commit: `refactor(server): Encapsulate agent and MCP initialization logic`**

- **Why**: The logic for initializing the agent and its MCP servers is complex
  and only runs on startup. It should be isolated from the request-handling
  code.
- **Actions**:
  1.  Create `src/server/agent_setup.py`.
  2.  Move the `_initialize_mcp_servers_and_agent` function into this new file.
  3.  Update the `on_startup` hook in `src/server.py` to call this new setup
      function.

### Step 5: Abstraction - Create a Dedicated Streaming Component

**Commit:
`refactor(server): Abstract LLM stream processing into LLMResponseStreamer`**

- **Why**: This is the core of the refactoring. The ~200-line `async for` loop
  in `handle_http_request` is the main source of complexity and should be its
  own component.
- **Actions**:
  1.  Create `src/server/streaming.py`.
  2.  Define a new class, `LLMResponseStreamer`.
  3.  Move the entire `async for event in agent_stream.stream_events():` loop
      and its associated logic (header parsing, body buffering, tool call
      handling, session recording) into a primary method on this class, e.g.,
      `async def stream_response(...)`.
  4.  Refactor `handle_http_request` to instantiate `LLMResponseStreamer` and
      delegate the streaming work to it. The handler will become much shorter.

### Step 6: Abstraction - Extract LLM-Generated Error Handling

**Commit:
`refactor(server): Move LLM error response generation to src/server/errors.py`**

- **Why**: Error handling is a distinct responsibility. A dedicated module makes
  it easier to manage and improve fallback strategies.
- **Actions**:
  1.  Create `src/server/errors.py`.
  2.  Move the `_send_llm_error_response_aiohttp` function into this file.
  3.  Update all call sites to use the function from its new location.

### Step 7: Framework Features - Introduce `aiohttp` Middlewares

**Commit:
`feat(server): Introduce middleware for logging, session, and error handling`**

- **Why**: Use the framework's intended mechanism for handling concerns that
  wrap the main request-response cycle. This drastically cleans up the main
  handler.
- **Actions**:
  1.  Create `src/server/middleware.py`.
  2.  Implement a `logging_and_metrics_middleware` that handles request timing
      (TTFT, total duration), token counting, and final structured logging.
  3.  Implement a `session_middleware` that extracts the session ID from the
      cookie and attaches the session object to the request (e.g.,
      `request['session']`).
  4.  Implement an `error_handling_middleware` that wraps the handler call in a
      `try...except` block and invokes the LLM error page generator.
  5.  Register these middlewares in `create_app`.

### Step 8: Finalization - Simplify the Core Application

**Commit:
`refactor(server): Simplify application wiring and the main request handler`**

- **Why**: With all components and middleware in place, the main application
  file and handler can be reduced to their essential role: wiring everything
  together.
- **Actions**:
  1.  Remove all the now-redundant logic from `handle_http_request` (logging,
      session lookups, error handling, etc.). It should now be a thin
      coordinator that:
      - Prepares the system prompt.
      - Runs the agent stream.
      - Passes the stream to the `LLMResponseStreamer`.
      - Returns the response.
  2.  Move the `create_app`, `on_startup`, `on_shutdown`, and
      `run_local_tools_stdio_server` functions out of `src/server.py` and into
      `src/app.py`.
  3.  Delete the now-empty `src/server.py`.
  4.  Update `pyproject.toml` if necessary to point to the new entry point
      (`src.app:create_app` or similar).

### Step 9: Polish

**Commit: `chore: Run formatter and linter on new server modules`**

- **Why**: Ensure code quality and consistency across the new modular structure.
- **Actions**:
  1.  Run `uv run ruff format .`
  2.  Run `uv run ruff check --fix .`
  3.  Perform a final manual review of the new file structure.
