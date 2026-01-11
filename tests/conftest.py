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
        # Load training data for path-based response lookup
        self._load_training_data()
    
    def _load_training_data(self):
        """Load training data and create path-to-response mapping"""
        import re
        from src.training_data import training_data
        
        self.path_responses = {}
        for example in training_data:
            # Extract method and path from http_request
            request_line = example.http_request.split('\r\n')[0]
            match = re.match(r'(\w+)\s+(\S+)\s+HTTP/', request_line)
            if match:
                method, path = match.groups()
                key = f"{method}:{path}"
                # Only use first occurrence of each path (first-wins policy)
                if key not in self.path_responses:
                    self.path_responses[key] = example.http_response
                    print(f"Loaded training response for {key}")
    
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
            # First request with tools available
            # Check if we have a matching training response - if so, return it directly
            import re
            http_method = "GET"
            http_path = "/"
            
            # Extract method and path from user message
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        match = re.search(r'(GET|POST|PUT|DELETE|HEAD|OPTIONS)\s+(\S+)\s+HTTP/', content)
                        if match:
                            http_method, http_path = match.groups()
                            print(f"Chat API: Extracted HTTP request: {http_method} {http_path}")
                            break
            
            lookup_key = f"{http_method}:{http_path}"
            print(f"Chat API: Looking up response for: {lookup_key}")
            
            if lookup_key in self.path_responses:
                # Return training response directly without calling tool
                print(f"Chat API: Returning training response for {lookup_key}")
                http_response = self.path_responses[lookup_key]
                
                if stream:
                    # Return streaming response for training data
                    return await self._create_streaming_training_response(
                        request, http_response
                    )
                else:
                    response = {
                        "id": "chatcmpl-test-training",
                        "object": "chat.completion",
                        "created": 1677652288,
                        "model": "gpt-3.5-turbo",
                        "choices": [{
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": http_response
                            },
                            "finish_reason": "stop"
                        }],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 50,
                            "total_tokens": 60
                        }
                    }
                    return web.json_response(response)
            
            # No matching training response, make tool call
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
        import re
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
        
        # Extract HTTP method and path from the user message to look up response
        http_method = "GET"
        http_path = "/"
        print(f"DEBUG: input_items = {json.dumps(input_items, default=str)[:500]}")
        for item in input_items:
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content", "")
                if isinstance(content, str):
                    # Look for HTTP request line pattern in user message
                    match = re.search(r'(GET|POST|PUT|DELETE|HEAD|OPTIONS)\s+(\S+)\s+HTTP/', content)
                    if match:
                        http_method, http_path = match.groups()
                        print(f"Extracted HTTP request: {http_method} {http_path}")
                        break
        
        lookup_key = f"{http_method}:{http_path}"
        print(f"Looking up response for: {lookup_key}")
        
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
        
        # If we have a matching training response, return it directly instead of calling tool
        elif lookup_key in self.path_responses:
            print(f"Responses API: Returning training response directly for {lookup_key}")
            http_response_text = self.path_responses[lookup_key]
            response = {
                **base_response,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_training",
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
        
        # Fallback - return a simple message response using path_responses lookup
        print(f"Responses API: Returning direct message response for {lookup_key}")
        
        # Look up the response from training data, or use default
        http_response_text = self.path_responses.get(
            lookup_key,
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Hello, world!"
        )
        print(f"Using response: {http_response_text[:100]}...")
        
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
                            "text": http_response_text,
                            "annotations": []
                        }
                    ]
                }
            ],
        }
        return web.json_response(response)
    
    async def _create_streaming_training_response(self, request, http_response):
        """Create streaming response for training data - returns HTTP response as content"""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        await response.prepare(request)
        
        # Send content chunk with the full HTTP response
        content_chunk = {
            "id": "chatcmpl-test-training",
            "object": "chat.completion.chunk",
            "created": 1677652288,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": http_response
                },
                "finish_reason": None
            }]
        }
        await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())
        
        # Send finish chunk
        finish_chunk = {
            "id": "chatcmpl-test-training",
            "object": "chat.completion.chunk",
            "created": 1677652288,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        await response.write(f"data: {json.dumps(finish_chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response
    
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
    await server.cleanup()


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
    # Skip DSPy compilation in tests - it requires real LLM calls
    old_env = os.environ.get("SKIP_DSPY_COMPILATION")
    os.environ["SKIP_DSPY_COMPILATION"] = "true"
    try:
        await server.start()
        test_client = await aiohttp_client(server.app)
        yield test_client
    finally:
        if old_env is None:
            os.environ.pop("SKIP_DSPY_COMPILATION", None)
        else:
            os.environ["SKIP_DSPY_COMPILATION"] = old_env
        await server.cleanup()