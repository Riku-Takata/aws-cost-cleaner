"""Exceptions and exit codes for aws-cost-cleaner."""

from __future__ import annotations

EXIT_OK: int = 0
EXIT_PARTIAL_FAILURE: int = 1
EXIT_USAGE: int = 2


class AwsCostCleanerError(Exception):
    """Base exception for aws-cost-cleaner."""


class ConfigError(AwsCostCleanerError):
    """Error in configuration."""


class CostCsvError(AwsCostCleanerError):
    """Error in cost CSV file."""


class ApprovalFileError(AwsCostCleanerError):
    """Error in approval file."""


class AccountMismatchError(ApprovalFileError):
    """AWS account ID mismatch in approval file."""


class DisallowedApiCallError(AwsCostCleanerError):
    """API call is not in allowed list."""


class ProtectedResourceError(AwsCostCleanerError):
    """Resource is protected by Keep=true."""


class NotCandidateError(AwsCostCleanerError):
    """Resource does not have CleanupCandidate=true."""


class StateMismatchError(AwsCostCleanerError):
    """Resource state does not match expected state or resource not found."""


class WaitPeriodNotElapsedError(AwsCostCleanerError):
    """Wait period has not elapsed yet."""


class AwsOperationError(AwsCostCleanerError):
    """AWS API operation failed."""

    def __init__(self, message: str, aws_error_code: str | None) -> None:
        super().__init__(message)
        self.aws_error_code: str | None = aws_error_code
