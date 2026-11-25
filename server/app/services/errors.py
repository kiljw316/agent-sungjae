from __future__ import annotations


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or None


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class InvalidState(DomainError):
    status_code = 409
    code = "INVALID_STATE"


class VersionConflict(DomainError):
    status_code = 409
    code = "VERSION_CONFLICT"
