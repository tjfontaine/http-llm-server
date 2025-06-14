# HTTP LLM Server

[![Self-Awareness Badge](https://img.shields.io/badge/self--awareness-surprisingly%20high-blueviolet)](https://github.com/tjfontaine/http-llm-server)

## What Is This?

An HTTP server that lets an LLM handle everything. No routes, no controllers, no
templates—just HTTP requests going straight to your LLM and whatever it decides
to send back.

## How It Works

1. HTTP request comes in
2. Server forwards it to the LLM
3. LLM generates a complete HTTP response
4. Server streams it back
5. Repeat until your GPU melts

The LLM handles routing, templating, business logic, session management, and
probably your taxes.

## Quick Start

```bash
# Install dependencies
uv pip sync pyproject.toml

# Set your API key
export OPENAI_API_KEY="your_key_here"

# Optional: Tell the LLM what kind of app to be
export WEB_APP_FILE="./examples/simple_blog/prompt.md"

# Run it
uv run python server.py
# or with arguments
uv run python server.py --port 8080
```

Visit `http://localhost:8080` and watch an AI pretend to be your entire web
stack.

## Configuration

- `--port` / `PORT`: Where to run (default: 8080)
- `--web-app-file` / `WEB_APP_FILE`: Markdown file with YAML front matter
  containing web app instructions and optional MCP server configuration
- `--api-key` / `OPENAI_API_KEY`: Your API key (required)
- `--model` / `OPENAI_MODEL_NAME`: Which model to use (default: gpt-4o)
- `--base-url` / `OPENAI_BASE_URL`: Custom OpenAI-compatible endpoint (e.g.,
  Ollama, vLLM, etc.)
- `--save-conversations` / `SAVE_CONVERSATIONS`: Save conversation history to
  files (default: false)

## Features

- **LLM-Generated Everything**: Status codes, headers, HTML, CSS, JavaScript
- **Session Management**: Via cookies that the AI manages itself
- **Streaming Responses**: Real-time AI thoughts delivered to your browser
- **Conversation History**: The LLM remembers your previous requests
- **Custom Applications**: Configure it to be a blog, todo app, or digital
  oracle
- **OpenAI-Compatible**: Works with OpenAI, Ollama, vLLM, or any
  OpenAI-compatible server
- **MCP Integration**: Connect external tools and data sources via Model Context
  Protocol

## The Economics

Every HTTP request triggers a full AI inference cycle. That's thousands of
tokens and enough GPU compute to power a small appliance, just to serve what
might be a simple "Hello World" page. We're trading developer time for compute
time at an exchange rate that would make economists weep.

## Should You Use This?

Not recommended for production use, unless you're feeling adventurous.

## Example Applications

Check out the `examples/` directory for pre-built applications:

- **`chat_app/prompt.md`**: Single-page chat interface
- **`data_dashboard/prompt.md`**: Analytics dashboard with database and web
  search capabilities
- **`default_info_site/prompt.md`**: Default informational website (used
  automatically if no web app file is specified)
- **`simple_blog/prompt.md`**: File-based blog with MCP filesystem access
- **`simple_todo/prompt.md`**: Todo app with persistent SQLite database

To use an example:

```bash
uv run start_server --web-app-file examples/simple_blog/prompt.md
```

If you don't specify a `--web-app-file`, the server automatically uses
`examples/default_info_site/prompt.md`.

## Configuration

### Web App Definition

Configure your AI web server using markdown files with YAML front matter:

```markdown
---
title: "My Web App"
description: "Powered by artificial intelligence and questionable decisions"
mcp_servers:
  - type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
author: "Someone"
version: "1.0"
---

# Instructions for your AI

Tell the AI exactly how to behave and what kind of website to be...
```

#### Configuration Fields

**Essential:**

- **`title`**: Application name
- **`description`**: Brief description
- **`mcp_servers`**: External tools the AI can access

**Optional:**

- **`author`**: Who to credit or blame
- **`version`**: For tracking iterations
- **`tags`**: Categorization
- **`dependencies`**: External requirements
- **`config`**: Custom settings

#### Best Practices

1. **Be Specific**: The AI is smart, but specificity helps
2. **Stay Organized**: Use headers and bullets for clarity
3. **Limit Access**: Don't give filesystem access unless necessary
4. **Version Everything**: Track what works
5. **Include Examples**: Show the AI what you want
6. **Plan for Edge Cases**: Define fallback behavior

### MCP Integration

MCP (Model Context Protocol) lets your AI access external tools and data
sources. Configure MCP servers in your web app's YAML front matter:

```yaml
mcp_servers:
  # Filesystem access
  - type: stdio
    command: npx
    args:
      ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"]

  # Database access
  - type: stdio
    command: uvx
    args: ["database-mcp"]
    env:
      DB_TYPE: sqlite
      DB_CONFIG: '{"dbpath": "/path/to/database.db"}'

  # HTTP streaming
  - type: streamable_http
    url: "http://localhost:3000/mcp"
```

#### How It Works

1. Server connects to configured MCP servers on startup
2. AI receives toolkit of available capabilities
3. HTTP requests trigger AI to use MCP tools as needed
4. AI incorporates external data into responses

#### What You Can Build

- **Dynamic Content**: AI reads files and includes live data
- **Database-Driven Sites**: AI queries databases and presents results
- **Research-Enhanced Responses**: AI fact-checks using web search
- **File Management**: AI can create, read, and modify files

#### Security Considerations

MCP servers inherit your permissions. Use read-only access where possible, and
limit filesystem access to specific directories. You're giving an AI access to
external systems—configure accordingly.

## Contributing

PRs welcome. You know what you're getting into.

## License

MIT License.
