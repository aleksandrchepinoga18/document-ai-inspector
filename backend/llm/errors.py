"""Sanitized errors: never include SDK exception bodies, headers or credentials."""


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, attempts: int = 0, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = attempts
        self.diagnostic = diagnostic
