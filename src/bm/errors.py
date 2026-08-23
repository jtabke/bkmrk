"""Application-level errors shared by core operations and the CLI boundary."""


class BmError(Exception):
    """Base class for expected bookmark-manager failures.

    Core operations raise these exceptions without printing or terminating the
    process. The CLI translates them into the established ``bm: ...`` error
    output and exit status.
    """

    exit_code = 1


class UnsafePathError(BmError):
    """Raised when user-controlled path input is unsafe."""


class NotFoundError(BmError):
    """Raised when a requested bookmark or store entry does not exist."""


class ConflictError(BmError):
    """Raised when a read/modify/write operation observes stale state."""


class AmbiguousEntryError(BmError):
    """Raised when a path-like token matches multiple entries."""
