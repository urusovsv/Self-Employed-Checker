"""Self Employed Checker — проверка статуса самозанятого через API ФНС России."""

from .checker import (
    APIError,
    InvalidDateError,
    InvalidInnError,
    SelfEmployedChecker,
    SelfEmployedCheckerError,
)

__version__ = "1.1.0"

__all__ = [
    "SelfEmployedChecker",
    "SelfEmployedCheckerError",
    "InvalidDateError",
    "InvalidInnError",
    "APIError",
    "__version__",
]
