"""Retention periods enforced by the cleanup_assistant command. The privacy notice reads the same values."""

SUBMISSION_CLAIM_DAYS = 7  # Duplicate-submission claims (actor key + token).
CLOSED_SESSION_DAYS = 7  # Live transcription session rows, counted from when they closed.
