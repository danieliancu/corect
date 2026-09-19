"""Event screens: a welcome, a congratulation, a "done" or a goodbye shown once over the next page, in the same frosted
style as the waiting screen (templates/partials/event_screen.html, static/js/event-screen.js). They travel as messages,
so they survive the redirect (and, for a deleted account, the sign-out) like any other message."""
from django.contrib import messages

WELCOME, PRO, UPDATED, GOODBYE = "welcome", "pro", "updated", "goodbye"
KINDS = (WELCOME, PRO, UPDATED, GOODBYE)
TAG = "event"


def announce(request, kind, text=""):
    """Queues one event screen. A request shows at most one; allauth's own banner for the same step is dropped
    (apps.accounts.adapters.AccountAdapter.add_message)."""
    assert kind in KINDS, kind
    if getattr(request, "corect_event", None):
        return
    request.corect_event = kind
    messages.success(request, text, extra_tags=f"{TAG} {kind}")


def event_kind(message):
    """The event's kind for a message queued by announce(), or "" for an ordinary message."""
    tags = message.extra_tags.split() if message.extra_tags else []
    return tags[1] if len(tags) == 2 and tags[0] == TAG and tags[1] in KINDS else ""
