from django import template

from apps.analytics.formatting import (format_audio_operation, format_duration, format_money, format_number,
                                       format_percent, format_usd)

register = template.Library()
register.filter("money", format_money)
register.filter("usd", format_usd)
register.filter("number", format_number)
register.filter("percent", format_percent)
register.filter("duration", format_duration)
register.filter("audio_operation", format_audio_operation)
