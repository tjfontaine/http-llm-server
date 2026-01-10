import json
import os
import sys
from pathlib import Path

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config import Config
from src.server.web_resource import WebServer


class MockOpenAIServer:
    """Mock OpenAI API server for testing - supports both Chat Completions and Responses API"""
    
    def __init__(self):
        self.app = web.Application()
        self._setup_routes()
        self.conversation_state = {}
    
    def _setup_routes(self):
        # Chat Completions API (legacy, for DSPy)
        self.app.router.add_post('/v1/chat/completions', self._handle_chat_completions)
        self.app.router.add_post('/chat/completions', self._handle_chat_completions)
        # Responses API (new, for openai-agents 0.6+)
        self.app.router.add_post('/v1/responses', self._handle_responses)
    
    async def _handle_chat_completions(self, request):
        """Handle chat completion requests"""
        print(f"Mock server received request: {request.method} {request.path}")
        data = await request.json()
        print(f"Request data: {data}")
        
        messages = data.get("messages", [])
        tools = data.get("tools", [])
        stream = data.get("stream", False)
        
        # Get conversation ID or create one
        conversation_id = str(id(messages))
        
        # Check if this is a follow-up after tool execution
        has_tool_message = any(msg.get("role") == "tool" for msg in messages)
        
        if has_tool_message:
            # This is after tool execution, extract HTTP response from tool result
            print("Tool execution complete, returning final response")
            
            # Find the tool message and extract the HTTP response
            tool_content = None
            for msg in messages:
                if msg.get("role") == "tool":
                    try:
                        # Parse the JSON content from tool result
                        tool_result = json.loads(msg.get("content", "{}"))
                        
                        # Extract the actual HTTP response from the nested structure
                        if "text" in tool_result:
                            inner_content = json.loads(tool_result["text"])
                            if "content" in inner_content and inner_content["content"]:
                                tool_content = inner_content["content"][0]["text"]
                                break
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
            
            # Use extracted content or fallback
            # For HTTP response tools, don't add any extra content
            # The tool output IS the complete response
            final_content = ""
            
            if stream:
                return await self._create_streaming_final_response(
                    request, final_content
                )
            else:
                response = {
                    "id": "chatcmpl-test-final",
                    "object": "chat.completion",
                    "created": 1677652288,
                    "model": "gpt-3.5-turbo",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": final_content
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15
                    }
                }
                return web.json_response(response)
        
        elif tools and conversation_id not in self.conversation_state:
            # First request with tools available, make tool call
            self.conversation_state[conversation_id] = True
            print("Making tool call to generate_http_response")
            
            if stream:
                return await self._create_streaming_tool_call(request)
            else:
                response = {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1677652288,
                    "model": "gpt-3.5-turbo",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_test_123",
                                "type": "function",
                                "function": {
                                    "name": "generate_http_response",
                                    "arguments": json.dumps({
                                        "context_str": "",
                                        "http_request": (
                                            "GET / HTTP/1.1\r\n"
                                            "Host: 127.0.0.1\r\n"
                                            "Accept: */*\r\n"
                                            "Accept-Encoding: gzip, deflate\r\n"
                                            "User-Agent: Python/3.13 "
                                            "aiohttp/3.12.13\r\n"
                                            "\r\n"
                                        )
                                    })
                                }
                            }]
                        },
                        "finish_reason": "tool_calls"
                    }],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "total_tokens": 30
                    }
                }
                return web.json_response(response)
        
        # Fallback for requests without tools
        print("Returning direct response (no tools available)")
        response = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello, world!"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }
        return web.json_response(response)
    
    async def _handle_responses(self, request):
        """Handle Responses API requests (openai-agents 0.6+)"""
        print(f"Mock Responses API received request: {request.method} {request.path}")
        data = await request.json()
        print(f"Responses API request data: {data}")
        
        input_items = data.get("input", [])
        tools = data.get("tools", [])
        stream = data.get("stream", False)
        
        # Track conversation state using a simple key
        conv_key = str(hash(json.dumps(input_items, sort_keys=True, default=str)))[:16]
        
        # Check if we have a function_call_output in input (tool result follow-up)
        has_tool_output = any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in input_items
        )
        
        import time
        base_response = {
            "id": f"resp_test_{int(time.time())}",
            "object": "response",
            "created_at": time.time(),
            "model": data.get("model", "gpt-4o"),
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": tools,
            "status": "completed",
        }
        
        if has_tool_output:
            # This is after tool execution - return the HTTP response directly
            print("Responses API: Tool execution complete, returning message with HTTP response")
            
            # Extract the HTTP response from the tool output
            http_response_text = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                "\r\n"
                "Hello, world!"
            )
            
            for item in input_items:
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    output = item.get("output", "")
                    if output.startswith("HTTP/"):
                        http_response_text = output
                        break
            
            response = {
                **base_response,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": http_response_text,
                                "annotations": []
                            }
                        ]
                    }
                ],
            }
            return web.json_response(response)
        
        elif tools and conv_key not in self.conversation_state:
            # First request with tools - make a function call to generate_http_response
            self.conversation_state[conv_key] = True
            print("Responses API: Making function call to generate_http_response")
            
            tool_call_args = json.dumps({
                "context_str": "",
                "http_request": (
                    "GET / HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Accept: */*\r\n"
                    "Accept-Encoding: gzip, deflate\r\n"
                    "User-Agent: Python/3.13 aiohttp/3.13.3\r\n"
                    "\r\n"
                )
            })
            
            response = {
                **base_response,
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_test_123",
                        "call_id": "call_test_123",
                        "name": "generate_http_response",
                        "arguments": tool_call_args,
                        "status": "completed"
                    }
                ],
            }
            return web.json_response(response)
        
        # Fallback - return a simple message response
        print("Responses API: Returning direct message response")
        response = {
            **base_response,
            "output": [
                {
                    "type": "message",
                    "id": "msg_fallback",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "HTTP/1.1 200 OK\r\n"
                                "Content-Type: text/plain; charset=utf-8\r\n"
                                "\r\n"
                                "Hello, world!"
                            ),
                            "annotations": []
                        }
                    ]
                }
            ],
        }
        return web.json_response(response)
    
    async def _create_streaming_tool_call(self, request):
        """Create streaming response for tool call"""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/plain'
        await response.prepare(request)
        
        # Tool call chunk
        tool_call_args = json.dumps({
            "context_str": "",
            "http_request": (
                "GET / HTTP/1.1\\r\\nHost: 127.0.0.1\\r\\nAccept: */*\\r\\n"
                "Accept-Encoding: gzip, deflate\\r\\n"
                "User-Agent: Python/3.13 aiohttp/3.12.13\\r\\n\\r\\n"
            )
        })
        
        tool_call_chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1677652288,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_test_123",
                        "type": "function",
                        "function": {
                            "name": "generate_http_response",
                            "arguments": tool_call_args
                        }
                    }]
                },
                "finish_reason": "tool_calls"
            }]
        }
        
        await response.write(f"data: {json.dumps(tool_call_chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response
    
    async def _create_streaming_final_response(self, request, content=None):
        """Create streaming response for final message"""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/plain'
        await response.prepare(request)
        
        final_content = content if content is not None else "Task completed successfully."
        
        final_chunk = {
            "id": "chatcmpl-test-final",
            "object": "chat.completion.chunk",
            "created": 1677652288,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": final_content
                },
                "finish_reason": "stop"
            }]
        }
        
        await response.write(f"data: {json.dumps(final_chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response


@pytest_asyncio.fixture
async def mock_openai_server():
    """Create a mock OpenAI API server"""
    mock_server = MockOpenAIServer()
    
    # Start the mock server
    async with TestServer(mock_server.app) as server:
        # Configure environment to use our mock server
        original_base_url = os.environ.get("OPENAI_BASE_URL")
        os.environ["OPENAI_BASE_URL"] = str(server.make_url("/"))
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["SKIP_DSPY_COMPILATION"] = "true"  # Skip DSPy compilation in tests
        
        try:
            yield mock_server
        finally:
            # Restore original environment
            if original_base_url:
                os.environ["OPENAI_BASE_URL"] = original_base_url
            elif "OPENAI_BASE_URL" in os.environ:
                del os.environ["OPENAI_BASE_URL"]


@pytest_asyncio.fixture
async def mock_openai_server_with_dspy():
    """Create a mock OpenAI API server with DSPy compilation enabled"""
    mock_server = MockOpenAIServer()
    
    # Start the mock server
    async with TestServer(mock_server.app) as server:
        # Configure environment to use our mock server
        original_base_url = os.environ.get("OPENAI_BASE_URL")
        original_skip_dspy = os.environ.get("SKIP_DSPY_COMPILATION")
        
        os.environ["OPENAI_BASE_URL"] = str(server.make_url("/"))
        os.environ["OPENAI_API_KEY"] = "test-key"
        # Do NOT set SKIP_DSPY_COMPILATION so compilation happens
        if "SKIP_DSPY_COMPILATION" in os.environ:
            del os.environ["SKIP_DSPY_COMPILATION"]
        
        try:
            yield mock_server
        finally:
            # Restore original environment
            if original_base_url:
                os.environ["OPENAI_BASE_URL"] = original_base_url
            elif "OPENAI_BASE_URL" in os.environ:
                del os.environ["OPENAI_BASE_URL"]
            
            if original_skip_dspy:
                os.environ["SKIP_DSPY_COMPILATION"] = original_skip_dspy


@pytest_asyncio.fixture
async def client(aiohttp_client, mock_openai_server):
    """Create test client with mock OpenAI server"""
    # Set up local tools MCP server configuration to enable 
    # the generate_http_response tool
    local_tools_config = {
        "type": "stdio",
        "cwd": str(Path(__file__).parent.parent),
        "module": "src.server.local_tools",
    }
    
    # Get the mock server URL for configuration
    mock_base_url = os.environ.get("OPENAI_BASE_URL", "")
    
    config = Config(
        openai_model_name="gpt-3.5-turbo",
        api_key="test-key",
        openai_base_url=mock_base_url,  # Use mock server for agents library
        mcp_servers=[local_tools_config]
    )
    
    server = WebServer(
        port=8080, 
        host="localhost", 
        config=config, 
        mcp_servers_config=[local_tools_config]
    )
    await server.start()
    test_client = await aiohttp_client(server.app)
    yield test_client
    await server.cleanup(force=True)


@pytest_asyncio.fixture
async def client_with_dspy(aiohttp_client, mock_openai_server_with_dspy):
    """Create test client with DSPy compilation enabled"""
    # Set up local tools MCP server configuration to enable 
    # the generate_http_response tool
    local_tools_config = {
        "type": "stdio",
        "cwd": str(Path(__file__).parent.parent),
        "module": "src.server.local_tools",
    }
    
    # Get the mock server URL for configuration
    mock_base_url = os.environ.get("OPENAI_BASE_URL", "")
    
    config = Config(
        openai_model_name="gpt-3.5-turbo",
        api_key="test-key",
        openai_base_url=mock_base_url,  # Use mock server for agents library
        mcp_servers=[local_tools_config]
    )
    
    server = WebServer(
        port=8080, 
        host="localhost", 
        config=config, 
        mcp_servers_config=[local_tools_config]
    )
    await server.start()
    test_client = await aiohttp_client(server.app)
    yield test_client
    await server.cleanup(force=True)