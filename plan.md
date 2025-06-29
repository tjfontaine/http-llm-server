# Refactoring Plan: From Monolithic Startup to Resource-Oriented Orchestration

### The Vision: From Imperative Monolith to Declarative, Resource-Oriented Services

This document outlines a step-by-step plan to fundamentally reshape the
application's architecture. We are moving away from a monolithic structure—where
server setup, business logic, and application wiring are tightly coupled in a
single startup script—towards a modern, modular system based on the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/specification/).

**The Problem (Why We're Refactoring):**

The application's startup and configuration logic is currently an imperative
script (`main.py`). This script is responsible for everything: parsing
command-line arguments, loading environment variables, reading prompt files,
assembling a complex configuration object, and finally, launching the `aiohttp`
server. This approach is brittle, hard to maintain, and difficult to extend.
Changing any aspect of the server's setup requires modifying this complex,
procedural code.

**The Solution: A Resource-Oriented Architecture**

The core of this refactor is a shift from a **procedural** style to a
**resource-oriented** one, driven by an orchestration agent. This aligns with
the core principles of MCP, which is designed to "standardize how to connect
LLMs with the context they need."

As the MCP specification states, we can think of our system in terms of:

- **Resources:** These are the "nouns" of our system—the data and state that the
  agent needs to interact with. In our case, the primary resource is the
  `WebServer` itself, but `File` content is also treated as a resource. The
  agent doesn't need to know _how_ to construct or manage these resources; it
  just needs to know how to address them (e.g.,
  `mcp://core-services/file/pyproject.toml`) and what they represent.

- **Tools:** These are the "verbs"—the actions that can be performed. Tools are
  the functions that the agent can call to manipulate resources or perform
  stateless operations. For example, `create_web_resource` is a tool that
  creates a new `WebServer` resource, and `start_web_server` is a tool that acts
  upon that resource.

By modeling our system this way, we move from a state where the startup script
knows _how_ to do everything, to a state where a high-level **Orchestrator
Agent** knows _what_ it wants. It delegates the complex implementation details
to a dedicated `core-services` MCP server, making the agent's instructions
dramatically simpler and the overall system more robust, maintainable, and
extensible.

---

### [X] Step 1: Introduce the Core Services MCP Server & Orchestrator

**Goal:** Lay the new architectural foundation by creating a `core-services` MCP
server and an orchestrator agent that communicates with it. This decouples the
server-setup logic from the main application entry point.

**Why This Step Matters:** This is the foundational change. It establishes the
core agent-server communication pipeline that every subsequent step will build
upon. By getting this right, we prove that the agent can successfully connect to
and communicate with the new `core-services` process, which is the heart of the
new architecture.

**Tasks:**

- [x] **Create `src/server/core_services.py`**: Instantiate a `fastmcp.Server`.
- [x] **Create `src/server/web_resource.py`**: Create an empty `WebServer` class
      as a placeholder.
- [x] **Refactor `main.py`**: Redesign it to load config, spawn the
      `core_services.py` subprocess, and run an "Orchestrator" agent.
- [x] **Create `src/prompts/orchestrator.md`**: Start with a placeholder prompt.
- [x] **Refactor `src/config.py`**: Use `pydantic-settings` for automatic
      environment variable loading.
- [x] **Deprecate Old Logic**: Remove old startup code from `src/app.py` and
      delete `src/server/agent_setup.py`.

**Test Plan:**

1.  Create a temporary file named `test_prompt.md` with the instruction:
    `Your only job is to list all available tools.`
2.  In `main.py`, temporarily point the orchestrator agent to use
    `test_prompt.md`.
3.  Run `python main.py`.
4.  **Verification**: The application should start, connect to the
    `core-services` subprocess, and the agent should report that it found "0
    tools". This confirms the entire communication pipeline is working correctly
    before we add any complexity.

**Git Commit Message:**
`refactor(arch): Introduce core-services MCP server and orchestrator agent`

---

### [X] Step 2: Implement `core-services` Tools for Resource Management

**Goal:** Populate the `core-services` MCP server with the granular tools needed
to configure and manage the `WebServer` resource.

**Why This Step Matters:** This step builds the "vocabulary" for our
orchestration agent. By creating discrete, single-purpose tools (like
`create_web_resource`, `start_web_server`, etc.), we make the server's
capabilities explicit and independently testable. It's like giving the agent a
set of well-defined LEGO bricks to build with, ensuring each piece works
perfectly on its own.

**Tasks:**

- [x] **Implement Utility Tools** in `src/server/core_services.py`:
      `get_config`, `read_file`, `render_template`, `parse_webapp_file`.
- [x] **Implement Resource Management Tools** in `src/server/core_services.py`:
      `create_web_resource`, `update_web_resource_config`, `connect_mcp_server`,
      `start_web_server`, `destroy_web_resource`.
- [x] **Flesh out `src/server/web_resource.py`**: Encapsulate `aiohttp` logic
      and lifecycle methods (`start`, `stop`, `cleanup`).
- [x] **Strip down `src/app.py`**: Leave only the `handle_http_request`
      function.

**Test Plan:**

1.  Update `test_prompt.md` to guide the agent through the full resource
    lifecycle:
    - "First, call `create_web_resource`. Then, call `start_web_server` with the
      returned `resource_id`. Wait 10 seconds. Finally, call
      `destroy_web_resource`."
2.  Run `python main.py`.
3.  **Verification**: During the 10-second window, use
    `curl http://localhost:8080` to verify the server is running. After the
    script finishes, the server should be shut down, and the `curl` command
    should fail. This verifies that our core resource management API (the "LEGO
    bricks") works as expected.

**Git Commit Message:**
`feat(mcp): Implement granular webserver resource API and tools`

---

### [X] Step 3: Create a High-Level `setup_web_app` Tool

**Goal:** Abstract the entire multi-step setup process into a single, high-level
tool, moving orchestration logic from the agent's prompt to the server.

**Why This Step Matters:** This is the key to simplifying the agent and making
the system robust. Instead of the agent needing to know the precise _sequence_
of tool calls, it can simply declare its high-level goal: `setup_web_app`. This
moves the complex orchestration logic from a brittle prompt into maintainable,
testable Python code on the server.

**Tasks:**

- [x] In `src/server/core_services.py`, create a new tool:
      `async def setup_web_app(context: Context)`.
- [x] Inside this function, implement the orchestration logic that is currently
      in the agent's prompt. It will call the other `core-services` tools
      _internally_ to perform the full setup sequence.

**Test Plan:**

1.  Update `test_prompt.md` to contain a single instruction:
    `Call the 'setup_web_app' tool.`
2.  Set the `WEB_APP_FILE` environment variable to a simple example, like
    `examples/simple_blog/prompt.md`.
3.  Run `python main.py`.
4.  **Verification**: The logs should show the `setup_web_app` tool being
    called, and then that tool's _own logs_ should show the sequence of internal
    steps being executed. The web server should be running at the end. Verify
    with `curl`. This proves the high-level abstraction works correctly.

**Git Commit Message:**
`feat(mcp): Add high-level setup_web_app tool for one-call orchestration`

---

### [ ] Step 4: Simplify the Final Orchestrator

**Goal:** Update the main `orchestrator.md` prompt to be a simple, declarative
instruction that uses the new high-level tool.

**Why This Step Matters:** This is the culmination of the entire refactoring.
The agent's instructions become dead simple, demonstrating the power and
elegance of the new architecture. The `orchestrator.md` prompt is now a single,
declarative goal, not a complex script, making it trivial to read and
understand.

**Tasks:**

- [ ] Replace the detailed, multi-step content in `src/prompts/orchestrator.md`
      with its final, simple form: "Call the `setup_web_app` tool."
- [ ] Ensure `main.py` points the orchestrator agent to this final prompt.

**Test Plan (End-to-End Verification):**

1.  Run the application normally: `uv run start`.
2.  **Verification**: The application should start, the orchestrator agent
    should run its simple prompt, call the single `setup_web_app` tool, and the
    fully configured web server should launch successfully.
3.  Verify with `curl http://localhost:8080`.
4.  Test the one-shot mode:
    `WEB_APP_FILE=examples/simple_blog/prompt.md uv run start -- --one-shot`.
    The server should start, log the one-shot response, and then shut down
    cleanly. This confirms the entire lifecycle works from a single command.

**Git Commit Message:**
`refactor(agent): Simplify orchestrator to use high-level setup_web_app tool`
