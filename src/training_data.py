"""Training data for the DSPy HttpProgram optimizer.

This provides examples to guide the DSPy optimizer in learning how to generate
appropriate HTTP responses for different request types.

Best practices for DSPy training data:
- 10+ examples recommended for BootstrapFewShot
- Cover diverse cases (routes, methods, status codes, content types)
- Use .with_inputs() to mark which fields are inputs vs outputs
"""

import dspy

# Training examples for the HTTP response generator
training_data = [
    # ========== Basic Routes (HTML) ==========
    dspy.Example(
        context="",
        http_request="GET / HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: curl/7.88.1\r\nAccept: */*\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<!DOCTYPE html>\n<html><head><title>Welcome</title></head>"
            "<body><h1>Welcome to the HTTP LLM Server</h1></body></html>"
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="GET /about HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: Mozilla/5.0\r\nAccept: text/html\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<!DOCTYPE html>\n<html><head><title>About</title></head>"
            "<body><h1>About This Project</h1><p>An AI-powered web server.</p></body></html>"
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="GET /features HTTP/1.1\r\nHost: localhost:8080\r\nAccept: text/html\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<!DOCTYPE html>\n<html><head><title>Features</title></head>"
            "<body><h1>Features</h1><ul><li>Dynamic responses</li><li>Session management</li></ul></body></html>"
        )
    ).with_inputs("context", "http_request"),
    
    # ========== API Endpoints (JSON) ==========
    dspy.Example(
        context='{"user_id": "123", "username": "alice"}',
        http_request="GET /api/user HTTP/1.1\r\nHost: localhost:8080\r\nAccept: application/json\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "\r\n"
            '{"user_id": "123", "username": "alice"}'
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context='{"status": "healthy", "uptime": 3600}',
        http_request="GET /api/status HTTP/1.1\r\nHost: localhost:8080\r\nAccept: application/json\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "\r\n"
            '{"status": "healthy", "uptime": 3600}'
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="POST /api/echo HTTP/1.1\r\nHost: localhost:8080\r\nContent-Type: application/json\r\n\r\n{\"message\": \"hello\"}",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "\r\n"
            '{"echo": {"message": "hello"}}'
        )
    ).with_inputs("context", "http_request"),
    
    # ========== Error Responses ==========
    dspy.Example(
        context="",
        http_request="GET /nonexistent HTTP/1.1\r\nHost: localhost:8080\r\nAccept: */*\r\n\r\n",
        http_response=(
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<!DOCTYPE html>\n<html><head><title>404 Not Found</title></head>"
            "<body><h1>404 Not Found</h1><p>The requested page was not found.</p></body></html>"
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="POST /api/data HTTP/1.1\r\nHost: localhost:8080\r\nContent-Type: text/plain\r\n\r\ninvalid",
        http_response=(
            "HTTP/1.1 400 Bad Request\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "\r\n"
            '{"error": "Invalid request format", "code": 400}'
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="DELETE /api/protected HTTP/1.1\r\nHost: localhost:8080\r\n\r\n",
        http_response=(
            "HTTP/1.1 405 Method Not Allowed\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Allow: GET, POST\r\n"
            "\r\n"
            '{"error": "Method not allowed", "code": 405}'
        )
    ).with_inputs("context", "http_request"),
    
    # ========== Plain Text Responses ==========
    dspy.Example(
        context="",
        http_request="GET /robots.txt HTTP/1.1\r\nHost: localhost:8080\r\nAccept: text/plain\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "User-agent: *\nAllow: /"
        )
    ).with_inputs("context", "http_request"),
    
    dspy.Example(
        context="",
        http_request="GET /_health_check HTTP/1.1\r\nHost: localhost:8080\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Cache-Control: no-cache\r\n"
            "\r\n"
            "OK"
        )
    ).with_inputs("context", "http_request"),
    
    # ========== Session-Aware Responses ==========
    dspy.Example(
        context='{"session_id": "abc-123", "visit_count": 5}',
        http_request="GET / HTTP/1.1\r\nHost: localhost:8080\r\nCookie: session_id=abc-123\r\nAccept: text/html\r\n\r\n",
        http_response=(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<!DOCTYPE html>\n<html><head><title>Welcome Back</title></head>"
            "<body><h1>Welcome back!</h1><p>You have visited 5 times.</p></body></html>"
        )
    ).with_inputs("context", "http_request"),
]
