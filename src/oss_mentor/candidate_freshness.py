"""Fail-closed, clock-aware checks shared by storage and recommendation routes."""
from datetime import datetime, timedelta, timezone
from typing import Any
from contextlib import contextmanager
import os

VERIFICATION_TTL = timedelta(hours=24)
ASSUME_INVENTORY_ENV = 'OSS_MENTOR_ASSUME_INVENTORY_AVAILABLE'


@contextmanager
def inventory_assumption(enabled: bool):
    """Explicit process-wide trial setting, shared by HTTP worker threads."""
    previous = os.environ.get(ASSUME_INVENTORY_ENV)
    try:
        if enabled:
            os.environ[ASSUME_INVENTORY_ENV] = '1'
        else:
            os.environ.pop(ASSUME_INVENTORY_ENV, None)
        yield
    finally:
        if previous is None:
            os.environ.pop(ASSUME_INVENTORY_ENV, None)
        else:
            os.environ[ASSUME_INVENTORY_ENV] = previous


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def verification_deadline(record: dict[str, Any]) -> datetime | None:
    verified = parse_timestamp(record.get('github_verified_at') or record.get('verified_at'))
    if verified is None:
        return None
    try:
        deadline = verified + VERIFICATION_TTL
    except OverflowError:
        return None
    explicit = record.get('verification_expires_at')
    if explicit is None:
        explicit = record.get('expires_at')
    if explicit is not None:
        original = parse_timestamp(explicit)
        if original is None:
            return None
        deadline = min(deadline, original)
    return deadline


def verification_is_current(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    verified = parse_timestamp(record.get('github_verified_at') or record.get('verified_at'))
    deadline = verification_deadline(record)
    return bool(verified and deadline and verified <= current < deadline)


def recommendation_is_current(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    return (record.get('candidate_availability', 'available') == 'available'
            and (os.environ.get(ASSUME_INVENTORY_ENV) == '1'
                 or verification_is_current(record, now=now)))
