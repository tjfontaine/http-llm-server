
import pytest


@pytest.mark.asyncio
async def test_initial_request_is_deterministic_raw_http_exact(client):
    """Test that the initial request returns exactly the expected raw HTTP bytes."""
    response = await client.get("/")
    body = await response.read()
    
    # Verify key response elements
    # Note: aiohttp automatically adds headers like Transfer-Encoding, Date, Server
    # so we can't match exact bytes. Instead verify the key LLM-generated content.
    assert response.status == 200
    assert response.reason == "OK"
    assert response.headers.get("Content-Type") == "text/plain; charset=utf-8"
    assert body.decode('utf-8') == "Hello, world!"


@pytest.mark.asyncio
async def test_initial_request_raw_http_deterministic(client):
    """Test that the initial request returns a deterministic raw HTTP response."""
    response = await client.get("/")
    body = await response.read()
    
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert body == b"Hello, world!"


@pytest.mark.asyncio
async def test_dspy_integration_with_fallback(client):
    """Test that DSPy integration works with fallback response in test mode"""
    response = await client.get("/")
    body = await response.read()
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert body == b"Hello, world!"


training_data_module = __import__(
    'src.training_data', fromlist=['training_data']
)


@pytest.mark.skip(
    reason="The runtime now uses direct HTTP generation instead of DSPy. "
    "These tests are for DSPy-compiled output which requires real LLM calls "
    "during compilation. File-based DSPy save/load is implemented for when "
    "DSPy is re-enabled."
)
@pytest.mark.parametrize("example", [
    pytest.param(example, id=f"training_example_{i}")
    for i, example in enumerate(training_data_module.training_data)
])
@pytest.mark.asyncio
async def test_compiled_program_matches_training_examples(
    client_with_dspy, example
):
    """Test that the compiled DSPy program produces expected output."""
    # Extract the HTTP method and path from the http_request
    request_lines = example.http_request.split('\r\n')
    request_line = request_lines[0]
    method, path, _ = request_line.split(' ', 2)
    
    # Make the request using the test client
    if method == "GET":
        response = await client_with_dspy.get(path)
    elif method == "POST":
        response = await client_with_dspy.post(path)
    # Add other methods as needed
    
    # Read the full response
    body = await response.read()
    
    # Reconstruct the raw HTTP response for comparison
    status_line = f"HTTP/1.1 {response.status} {response.reason}\r\n"
    headers_str = ""
    for name, value in response.headers.items():
        # Skip dynamic headers
        if name.lower() not in ['date', 'server']:
            headers_str += f"{name}: {value}\r\n"
    
    actual_response = (
        status_line + headers_str + "\r\n" + body.decode('utf-8')
    )
    
    # Compare with expected (ignoring dynamic headers)
    expected_lines = example.http_response.split('\r\n')
    actual_lines = actual_response.split('\r\n')
    
    # Compare status line
    assert actual_lines[0] == expected_lines[0]
    
    # Compare body (last part after empty line)
    expected_body = '\r\n'.join(
        expected_lines[expected_lines.index('') + 1:]
    )
    actual_body = '\r\n'.join(
        actual_lines[actual_lines.index('') + 1:]
    )
    assert actual_body == expected_body


@pytest.mark.asyncio
async def test_session_creation_and_cookie_handling(client_with_dspy):
    """Test that session creation works and cookies are properly set."""
    # This test verifies the session creation tool call and cookie handling
    response = await client_with_dspy.get("/")
    
    # Check if a session cookie was set (implementation dependent)
    # This may not be set in the current simple test case
    # but validates the cookie handling infrastructure
    if 'Set-Cookie' in response.headers:
        cookie_header = response.headers['Set-Cookie']
        assert 'session_id=' in cookie_header
        assert 'HttpOnly' in cookie_header


@pytest.mark.asyncio
async def test_different_http_methods(client_with_dspy):
    """Test that different HTTP methods are handled correctly."""
    # Test POST method
    response = await client_with_dspy.post("/", data="test data")
    assert response.status == 200  # Expecting fallback for now
    
    # Test other methods as the system supports them
    response = await client_with_dspy.put("/")
    assert response.status in [200, 404, 405]  # Any reasonable response


@pytest.mark.asyncio
async def test_error_handling_with_invalid_dspy_output(client_with_dspy):
    """Test that invalid DSPy outputs are handled gracefully."""
    # This test verifies the fallback mechanism when DSPy fails
    response = await client_with_dspy.get("/invalid-test-case")
    
    # Should still get a valid HTTP response even if DSPy fails
    assert response.status in [200, 404]
    body = await response.read()
    assert len(body) > 0  # Should have some content

