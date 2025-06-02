# HTTP LLM Server: Because Why Not Let AI Handle Everything?

[![Self-Awareness Badge](https://img.shields.io/badge/self--awareness-surprisingly%20high-blueviolet)](https://github.com/tjfontaine/http-llm-server)

## What Is This Madness?

An HTTP server that lets an LLM handle **everything**. No routes, no controllers, no templates—just raw HTTP requests going straight to your LLM and whatever it decides to send back.

Yes, this is real. No, your production team shouldn't use this. Yes, it's weirdly fascinating.

## How It Works (The Absurd Version)

1. HTTP request comes in
2. Server says "hey LLM, deal with this"
3. LLM pretends to be a web server and generates a complete HTTP response
4. Server streams it back like nothing weird happened
5. Somehow it actually works

The LLM handles routing, templating, business logic, session management, and probably your existential crisis too.

## Quick Start

```bash
# Install stuff
uv pip sync pyproject.toml

# Set your API key (required for OpenAI, or your provider's key)
export OPENAI_API_KEY="your_key_here"

# Optional: Tell the LLM what kind of app to pretend to be
export WEB_APP_FILE="./my_app_prompt.txt"

# Run the chaos
uv run python server.py
# or with arguments
uv run python server.py --port 8080
```

Visit `http://localhost:8080` and watch an AI cosplay as your entire web stack.

## Configuration

- `--port` / `PORT`: Where to run (default: 8080)
- `--web-app-file` / `WEB_APP_FILE`: Instructions for what web app the LLM should roleplay
- `--api-key` / `OPENAI_API_KEY`: Your API key (required)
- `--model` / `OPENAI_MODEL_NAME`: Which model to traumatize (default: gpt-4o)
- `--base-url` / `OPENAI_BASE_URL`: Custom OpenAI-compatible endpoint (e.g., Ollama, vLLM, etc.)
- `--save-conversations` / `SAVE_CONVERSATIONS`: Save conversation history to files (default: false)

## Features That Shouldn't Work But Do

- **LLM-Generated Everything**: Status codes, headers, HTML, the works
- **Session Management**: Via cookies that the AI manages
- **Streaming Responses**: Because why wait for the AI to finish its thoughts?
- **Conversation History**: The LLM remembers your previous requests
- **Custom Apps**: Tell it to be a todo app, blog, or sentient calculator
- **OpenAI-Compatible**: Works with OpenAI, Ollama, vLLM, or any OpenAI-compatible server

## Why Does This Exist?

Good question. Probably because I wondered "what if we just... didn't write a web server?" and then actually tried it.

It's like giving a very smart parrot the keys to your web infrastructure and being surprised when it doesn't immediately crash and burn.

## Should You Use This?

**For production?** Absolutely not.  
**For your banking app?** Please no.  
**For fun experiments?** Why not?  
**To confuse your coworkers?** Definitely.

## Contributing

I'm not sure why you would?

## License

MIT License, because even chaos needs proper licensing.
