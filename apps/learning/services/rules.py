"""Every threshold the learning engine applies, in one place. Pure product rules: no AI decides any of these."""

RECENT_DAYS = 30  # "Recent" occurrences; the previous period is the 30 days before.
RECURRING_RECENT = 2  # Occurrences in the recent period that make a pattern recurring...
RECURRING_TOTAL = 3  # ...or occurrences in total.
RECENT_ATTEMPTS = 10  # Practice accuracy is measured over the latest attempts.
ATTEMPT_WINDOW_DAYS = 90
IMPROVING_MIN_ATTEMPTS = 3
IMPROVING_ACCURACY = 0.6
IMPROVING_QUIET_DAYS = 14  # Or fewer occurrences than the previous period.
MASTERED_MIN_ATTEMPTS = 5
MASTERED_ACCURACY = 0.8
MASTERED_QUIET_DAYS = 21

REVIEW_SCHEDULE_DAYS = (1, 3, 7, 14, 30)
MASTERED_REINFORCEMENT_DAYS = 45
SESSION_SUCCESS = 0.8  # Session accuracy for a pattern that moves its review later...
SESSION_FAILURE = 0.5  # ...and below which the review comes back sooner.

TODAY_EXERCISES = 5
TODAY_SPLIT = (2, 2, 1)  # Exercises for the first, second and third priority.
TOP_PATTERNS = 3

EXERCISE_REST_DAYS = 7  # An exercise answered recently is not shown again yet.
EXERCISE_RETIRE_CORRECT = 2
EXERCISE_RETIRE_SHOWN = 4
EXERCISE_MAX_AGE_DAYS = 90
MIN_VALID_GENERATED = 3

INSIGHT_MIN_CORRECTIONS = 5  # Trends are only claimed with enough data...
INSIGHT_MIN_HISTORY_DAYS = 14  # ...and enough history.
INSIGHT_MIN_PREVIOUS = 3  # Occurrences in the previous period needed to compare periods.
INSIGHT_IMPROVED_RATIO = 0.7
INSIGHT_PERSISTENT_RECENT = 3
INSIGHT_ALMOST_ATTEMPTS = 5
INSIGHT_ALMOST_ACCURACY = 0.7
PROFILE_REFRESH_MINUTES = 60  # Time-dependent scores are recalculated at most this often when a page is opened.
