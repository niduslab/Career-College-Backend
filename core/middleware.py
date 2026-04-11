"""Reusable middleware classes used across apps."""


class RequestLoggingMiddleware:
    """Simple request logger placeholder for future project-wide logging."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
