class PaymentError(Exception):
    """Raised on payment business-rule or gateway violations.

    Carries the HTTP status the view should return — same pattern as
    `WebinarError` / `AssignmentSubmissionError`.
    """

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
