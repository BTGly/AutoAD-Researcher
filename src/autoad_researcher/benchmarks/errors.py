"""Internal benchmark preflight error types."""

class BenchmarkPreflightError(RuntimeError):
    def __init__(self, *, check_name: str, code: str, message: str) -> None:
        super().__init__(message)
        self.check_name = check_name
        self.code = code
        self.message = message
