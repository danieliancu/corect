from django import template

from apps.core.events import event_kind

register = template.Library()


@register.filter
def event_screens(messages):
    """base.html shows announce() messages as event screens: [(kind, message)]."""
    return [(event_kind(message), message) for message in messages if event_kind(message)]


@register.filter
def banners(messages):
    """Every other message keeps the usual banner."""
    return [message for message in messages if not event_kind(message)]
