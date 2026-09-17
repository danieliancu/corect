"""What the dashboard's figures say, in a sentence each.

A pure function over aggregates the view has already fetched: it runs no query of its own, so the per-row query counts
the report tests assert stay exactly as they were. Nothing is shown unless the data supports it — an empty period
produces an empty list, and the panel says so rather than inventing a trend.
"""

# A failure rate above this is worth acting on; below it, the same figure is reported as healthy.
FAILURE_ALERT = 0.05
# A plan tier where this share of active accounts used up the daily quota is under real pressure.
QUOTA_PRESSURE = 0.25
# Below this share of visitors signing up, conversion is worth a look rather than a celebration.
SIGNUP_GOOD = 0.05
# Cost sources are only called out once the period has enough spend for the share to mean anything.
MIN_COST_ROWS = 10

SOURCE_LABELS = {"text": "Text AI", "learning": "Learning AI", "stt": "Voice input", "tts": "Voice output"}


def _percent(value):
    return f"{value * 100:.1f}%" if value is not None else "—"


def _insight(level, icon, title, detail):
    """`icon` names a file in templates/analytics/partials/icons/, chosen for what the insight says."""
    return {"level": level, "icon": icon, "title": title, "detail": detail}


def dashboard_insights(*, totals, costs, audio_totals, learning_totals, plan_rows, visitors, signup_rate,
                       success_rate, plan_conversions):
    """The notable facts about this period, most actionable first."""
    insights = []
    requests = totals.get("requests") or 0
    failures = totals.get("failures") or 0
    rejections = totals.get("rejections") or 0

    # Where the money goes. Only once there is enough spend for a share to be meaningful.
    if requests >= MIN_COST_ROWS and costs.get("total"):
        shares = {key: costs.get(f"{key}_share") for key in SOURCE_LABELS}
        known = {key: value for key, value in shares.items() if value is not None}
        if known:
            leader = max(known, key=known.get)
            insights.append(_insight(
                "info", "coins", f"{SOURCE_LABELS[leader]} leads the AI cost",
                f"{_percent(known[leader])} of the period's AI cost. "
                + " · ".join(f"{SOURCE_LABELS[key]} {_percent(value)}" for key, value in known.items() if key != leader)))

    # How healthy the text action is.
    if requests:
        failure_rate = failures / requests
        if failure_rate >= FAILURE_ALERT:
            insights.append(_insight(
                "alert", "alert", f"{_percent(failure_rate)} of text requests failed",
                f"{failures} failed of {requests}. The errors table below groups them by code."))
        elif success_rate is not None:
            insights.append(_insight(
                "good", "check", f"Text success rate {_percent(success_rate)}",
                f"{failures} failed and {rejections} were refused by a rule (quota, rate limit, duplicate)."))

    # Which plan tier is running out of its daily allowance.
    for row in plan_rows:
        if row.get("hit_rate") is not None and row["hit_rate"] >= QUOTA_PRESSURE and row.get("active"):
            insights.append(_insight(
                "watch", "tag", f"{row['label']}: {_percent(row['hit_rate'])} used up the daily quota",
                f"{row['hit']} of {row['active']} active identities hit the limit. "
                f"{row['quota_rejections']} request{'' if row['quota_rejections'] == 1 else 's'} were refused."))

    # Anonymous visitors turning into accounts.
    if visitors.get("total"):
        level = "good" if signup_rate is not None and signup_rate >= SIGNUP_GOOD else "info"
        detail = f"{visitors['signed_up']} of {visitors['total']} visitors signed up; {visitors['signed_in']} signed in."
        if plan_conversions:
            detail += f" {plan_conversions} signed up or in after using up the anonymous quota."
        insights.append(_insight(level, "trend", f"Signup conversion {_percent(signup_rate)}", detail))

    # Voice is the most variable cost, so its share is worth stating on its own.
    if audio_totals.get("audio_calls") and costs.get("audio_share") is not None:
        insights.append(_insight(
            "info", "mic", f"Voice is {_percent(costs['audio_share'])} of AI cost",
            f"{audio_totals.get('transcriptions') or 0} transcriptions and "
            f"{audio_totals.get('speech_plays') or 0} British speech plays."))

    # Data quality: figures that cannot be trusted because a price or a token count was never recorded.
    unpriced = ((totals.get("without_cost") or 0) + (learning_totals.get("learning_without_cost") or 0)
                + (audio_totals.get("audio_without_cost") or 0))
    if unpriced:
        insights.append(_insight(
            "watch", "info", f"{unpriced} call{'' if unpriced == 1 else 's'} without pricing",
            "Their cost is missing from the totals: pricing was unavailable, or the row predates cost tracking."))

    return insights
