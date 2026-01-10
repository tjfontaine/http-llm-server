"""DSPy metric function for validating HTTP responses."""


def http_response_metric(example, pred, trace=None):
    """
    Validate HTTP response quality.
    
    Returns a score between 0.0 and 1.0 based on response correctness:
    - 1.0: Well-formed HTTP response with all required elements
    - 0.7: Valid status line and headers but Content-Type may be missing
    - 0.5: Valid status line but missing header/body separator
    - 0.3: Has HTTP prefix but malformed
    - 0.0: Not a valid HTTP response
    
    Args:
        example: The training example with expected output
        pred: The prediction from the DSPy program
        trace: Optional trace of intermediate LM calls (for optimization)
    """
    response = pred.http_response
    
    # Check starts with valid HTTP status line
    if not response.startswith("HTTP/1.1 "):
        return 0.0
    
    # Parse status line
    lines = response.split("\r\n")
    if len(lines) < 2:
        lines = response.split("\n")  # Fallback for \n only
    
    status_line = lines[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 3:
        return 0.3  # Malformed status line
    
    # Validate status code is numeric
    try:
        status_code = int(parts[1])
        if not (100 <= status_code <= 599):
            return 0.3
    except ValueError:
        return 0.3
    
    # Check has blank line separating headers from body
    has_separator = "\r\n\r\n" in response or "\n\n" in response
    if not has_separator:
        return 0.5
    
    # Check has required headers
    if "Content-Type:" not in response:
        return 0.7
    
    # If we have expected output, compare key elements
    if hasattr(example, 'http_response'):
        expected = example.http_response
        
        # Parse expected status code
        expected_parts = expected.split(" ", 2)
        if len(expected_parts) >= 2:
            try:
                expected_code = int(expected_parts[1])
                # Bonus for matching status code
                if status_code == expected_code:
                    return 1.0
                # Penalty for wrong status code class (e.g., 200 vs 404)
                if status_code // 100 != expected_code // 100:
                    return 0.8
            except ValueError:
                pass
    
    return 1.0


def strict_http_metric(example, pred, trace=None):
    """
    Stricter HTTP metric that also validates body content type consistency.
    
    Use this during evaluation, not during optimization (too strict).
    """
    base_score = http_response_metric(example, pred, trace)
    if base_score < 0.7:
        return base_score
    
    response = pred.http_response
    
    # Check Content-Type matches body
    if "application/json" in response:
        # Body should look like JSON
        parts = response.split("\r\n\r\n", 1)
        if len(parts) < 2:
            parts = response.split("\n\n", 1)
        if len(parts) >= 2:
            body = parts[1].strip()
            if not (body.startswith("{") or body.startswith("[")):
                return base_score * 0.9
    
    if "text/html" in response:
        # Body should look like HTML
        parts = response.split("\r\n\r\n", 1)
        if len(parts) < 2:
            parts = response.split("\n\n", 1)
        if len(parts) >= 2:
            body = parts[1].strip().lower()
            if not ("<html" in body or "<!doctype" in body or "<" in body):
                return base_score * 0.9
    
    return base_score
