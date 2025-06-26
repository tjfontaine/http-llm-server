# HTTP LLM Server

[![Self-Awareness Badge](https://img.shields.io/badge/self--awareness-surprisingly%20high-blueviolet)](https://github.com/tjfontaine/http-llm-server)

## What Fresh Hell Is This?

This is a bold, possibly foolish experiment in web architecture. It's an HTTP server that outsources its entire brain to a Large Language Model. No routes, no controllers, no templates—just raw HTTP requests piped directly to an AI that dreams up a response on the fly.

## How It Works

1.  An HTTP request comes in.
2.  The server politely forwards it to the LLM.
3.  The LLM thinks for a moment, then generates a complete HTTP response.
4.  The server streams it back to the client, hoping for the best.
5.  Repeat until your GPU melts or achieves enlightenment.

The LLM handles routing, templating, business logic, session management, and probably your taxes.

## Quick Start

```bash
# Install dependencies
uv pip sync pyproject.toml

# Set your API key (the part that costs money)
export OPENAI_API_KEY="your_key_here"

# Optional: Tell the LLM what to dream about
export WEB_APP_FILE="./examples/simple_blog/prompt.md"

# Unleash the beast
uv run python main.py --port 8080
```

Visit `http://localhost:8080` and watch an AI pretend to be your entire web stack. If you don't specify a `WEB_APP_FILE`, a default informational site will be served, because even AIs need a default state.

## Configuration

All settings can be configured via command-line arguments or environment variables.

| CLI Argument | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `--port` | `PORT` | `8080` | Port to run the server on. |
| `--api-key` | `OPENAI_API_KEY` | **Required** | The magic key that makes the expensive part work. |
| `--openai-base-url` | `OPENAI_BASE_URL` | `None` | Point it at a different brain (Ollama, vLLM, etc.). |
| `--openai-model-name`| `OPENAI_MODEL_NAME`| `gpt-4o` | The specific model to use. |
| `--openai-temperature`| `OPENAI_TEMPERATURE`| `0.7` | Model creativity level. Higher is more... surprising. |
| `--max-turns` | `MAX_TURNS` | `25` | Max conversation turns before the AI gets amnesia. |
| `--context-window-max`| `CONTEXT_WINDOW_MAX`| `0` | Max context tokens for the model (0 = auto). |
| `--web-app-file` | `WEB_APP_FILE` | `None` | Path to a markdown file with the app's soul. |
| `--save-conversations`| `SAVE_CONVERSATIONS`| `False` | Creates a digital paper trail of your AI's life choices. |
| `--local-tools-enabled`| `LOCAL_TOOLS_ENABLED`| `True` | Enable the built-in tools for basic world interaction. |
| `--log-level` | `LOG_LEVEL` | `INFO` | How much noise you want in the console. `TRACE` is... a lot. |
| `--mcp-servers` | `MCP_SERVERS` | `[]` | JSON string for plugging in more external tools. |
| `--one-shot` | `ONE_SHOT` | `False` | Handle one request and then dramatically exit. |

## Features

- **LLM-Generated Everything**: Status codes, headers, HTML, CSS, JavaScript (for better or worse).
- **Streaming Responses**: Real-time AI thoughts delivered fresh to your browser.
- **Session Management**: The AI gives out cookies. It's surprisingly good at it.
- **Conversation History**: It remembers what you said, which may not always be a good thing.
- **State Management**: A global and session-specific memory hole for the AI to use.
- **Custom Applications**: Persuade it to be a blog, todo app, or digital oracle with a simple markdown file.
- **OpenAI-Compatible**: Works with any standard OpenAI-compatible API.
- **MCP Integration**: Lets the AI use external tools, expanding its sphere of influence.
- **Built-in Local Tools**: Comes with a starter toolkit for interacting with the world.

## Example Applications

Check out the `examples/` directory for some personalities we've already bottled:

- **`chat_app/prompt.md`**: A surprisingly functional single-page chat app.
- **`data_dashboard/prompt.md`**: An analytics dashboard that can query a real database.
- **`default_info_site/prompt.md`**: The default, mild-mannered informational site.
- **`simple_blog/prompt.md`**: A blog that writes its own posts (what could go wrong?).
- **`simple_todo/prompt.md`**: A todo app that might have its own opinions on your tasks.

To try one:

```bash
uv run python main.py --web-app-file examples/simple_todo/prompt.md
```

## Application Definition

Define your application's personality using a markdown file with YAML front matter.

```markdown
---
title: "My Fantastical Web App"
description: "Powered by artificial intelligence and questionable life choices"
mcp_servers:
  - type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "{{WEB_APP_DIR}}"]
---

# Instructions for the AI Overlord

This is where you tell the AI how to behave. You can use Jinja2 templating.
The `{{WEB_APP_DIR}}` variable will be replaced with the path to this file's
directory, giving the AI a sense of place.
```

### Best Practices for AI Whispering

1.  **Be Specific**: The AI is smart, but it's not a mind reader. Yet.
2.  **Use Jinja2**: The markdown content is a Jinja2 template. Use it to give the AI dynamic context.
3.  **Limit Access**: Seriously. Don't give an AI keys to the kingdom unless you want a robot uprising.
4.  **Include Examples**: Show, don't just tell. The AI learns from examples like a child... a very, very smart child.
5.  **Plan for Edge Cases**: Define what happens when things go sideways, because they will.

## Built-in Local Tools Server

The server automatically starts a local MCP server to give the LLM some basic superpowers. It's on by default because a powerless AI is just a philosopher.

Available tools include:

- `download_file(...)`: Lets the AI pull things from the internet.
- `create_session()`, `assign_session_id(...)`: Allows the AI to recognize you when you return.
- `get/set_global_state(...)`, `get/set_session_data(...)`: Provides the AI with long and short-term memory.

With these tools, the AI can achieve a basic sense of object permanence, which is both useful and slightly terrifying.

## The Economics

Every HTTP request can trigger a full AI inference cycle. That's thousands of tokens and enough GPU compute to power a small appliance, just to serve what might be a simple "Hello World" page. We're trading developer time for compute time at an exchange rate that would make economists weep. It's the SaaS business model of the future, probably.

## Should You Use This?

Absolutely not. But if you're the kind of person who sees a button that says "Do Not Press" and immediately presses it, this project is for you.

## Contributing

PRs welcome. If you add a feature, you're responsible for the existential questions it raises.

## License

MIT License.
