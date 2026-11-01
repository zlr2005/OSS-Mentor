"""Offline publication gate; never tests credentials against a provider."""
import re

PATTERN = re.compile(rb'hf_[A-Za-z0-9]{20,}|cmVmd[A-Za-z0-9+/=]{30,}')
MARKER = b'[REDACTED_PUBLICATION_SECRET]'


def contains_secret(data: bytes) -> bool:
    return PATTERN.search(data) is not None


def require_publication_safe(data: bytes) -> None:
    if contains_secret(data):
        raise ValueError('Publication blocked: credential-like content requires redaction')


def redact(data: bytes) -> bytes:
    return PATTERN.sub(MARKER, data)
