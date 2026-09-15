"""Retention periods enforced by the cleanup_assistant command. The privacy notice reads the same values."""

SUBMISSION_CLAIM_DAYS = 7  # Duplicate-submission claims (actor key + token).
CLOSED_SESSION_DAYS = 7  # Live transcription session rows, counted from when they closed.
NATURALIZE_USAGE_DAYS = 2  # Daily plan-quota counters (actor key, day, count), counted from the end of their day.
