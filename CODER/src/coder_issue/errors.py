"""Domain-specific exceptions for predictable command-line failures."""


class CoderIssueError(Exception):
    """Base exception for the isolated algorithm project."""


class ConfigurationError(CoderIssueError):
    """Raised when a configuration file is missing or invalid."""


class DataFileError(CoderIssueError):
    """Raised when a required local data file cannot be inspected safely."""
