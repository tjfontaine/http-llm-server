
import dspy

# A minimalist training set for the HttpProgram
# This provides a few examples to guide the DSPy optimizer.

training_data = [
    dspy.Example(
        context="",
        http_request="GET / HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: curl/7.88.1\r\nAccept: */*\r\n\r\n",
        http_response="HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: 13\r\n\r\nHello, world!"
    ).with_inputs("context", "http_request"),
    dspy.Example(
        context='{\"user_id\": \"123\"}',
        http_request="GET /user HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: curl/7.88.1\r\nAccept: */*\r\n\r\n",
        http_response="HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: 18\r\n\r\n{\"user_id\": \"123\"}"
    ).with_inputs("context", "http_request"),
    dspy.Example(
        context="",
        http_request="GET /not_found HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: curl/7.88.1\r\nAccept: */*\r\n\r\n",
        http_response="HTTP/1.1 404 Not Found\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: 9\r\n\r\nNot Found"
    ).with_inputs("context", "http_request"),
]
