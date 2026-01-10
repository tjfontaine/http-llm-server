
import dspy

class GenerateHttpResponse(dspy.Signature):
    """Generate an HTTP response.

    You are an expert at generating raw HTTP responses. You need to generate a 
    complete and valid HTTP response, including the status line, headers 
    (including Content-Type), and body.
    """
    context = dspy.InputField(desc="The context for the request.")
    http_request = dspy.InputField(desc="The raw HTTP request.")
    http_response = dspy.OutputField(desc="The raw HTTP response.")

class HttpProgram(dspy.Module):
    """A DSPy program for generating HTTP responses."""
    def __init__(self):
        super().__init__()
        self.generate_response = dspy.Predict(
            GenerateHttpResponse,
            
        )

    def forward(self, context, http_request):
        return self.generate_response(context=context, http_request=http_request)
