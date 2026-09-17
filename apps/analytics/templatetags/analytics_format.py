from django import template

from apps.analytics.formatting import (format_audio_operation, format_duration, format_money, format_number,
                                       format_percent, format_usd, insight_icon, meter_width)

register = template.Library()
register.filter("money", format_money)
register.filter("usd", format_usd)
register.filter("number", format_number)
register.filter("percent", format_percent)
register.filter("duration", format_duration)
register.filter("audio_operation", format_audio_operation)
register.filter("meter_width", meter_width)
register.filter("insight_icon", insight_icon)
