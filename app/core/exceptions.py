from typing import Any, Optional


class ServiceException(Exception):
    """Base exception for all service-level errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        details: Optional[list[dict[str, Any]]] = None,
        status_code: int = 500
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or []
        self.status_code = status_code


class EntityNotFoundException(ServiceException):
    def __init__(self, message: str, details: Optional[list[dict[str, Any]]] = None):
        super().__init__(
            message=message,
            code="ENTITY_NOT_FOUND",
            details=details,
            status_code=404
        )


class ValidationException(ServiceException):
    def __init__(self, message: str, details: Optional[list[dict[str, Any]]] = None):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details=details,
            status_code=422
        )


class DisambiguationRequiredException(ServiceException):
    def __init__(self, message: str, details: Optional[list[dict[str, Any]]] = None):
        super().__init__(
            message=message,
            code="DISAMBIGUATION_REQUIRED",
            details=details,
            status_code=300
        )


class UpstreamTimeoutException(ServiceException):
    def __init__(self, message: str, details: Optional[list[dict[str, Any]]] = None):
        super().__init__(
            message=message,
            code="UPSTREAM_TIMEOUT",
            details=details,
            status_code=504
        )
