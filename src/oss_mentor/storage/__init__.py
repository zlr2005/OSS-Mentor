"""Business-specific storage adapters.

Shared storage protocols and transaction boundaries remain owned by the platform
module. Candidate persistence lives here so the data-sync feature can evolve
without adding business methods to the legacy SQLite facade.
"""

from oss_mentor.storage.candidates import (
    CandidateStorage,
    SQLiteCandidateStorage,
    SyncAlreadyRunningError,
    as_candidate_storage,
)
from oss_mentor.storage.base import MidtermProfileStore
from oss_mentor.storage.profiles import SQLiteMidtermProfileStore

__all__ = [
    "CandidateStorage",
    "MidtermProfileStore",
    "SQLiteCandidateStorage",
    "SQLiteMidtermProfileStore",
    "SyncAlreadyRunningError",
    "as_candidate_storage",
]
