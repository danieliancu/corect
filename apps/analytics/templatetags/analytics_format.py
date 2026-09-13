from django import template

from apps.analytics.formatting import format_money, format_number, format_percent

register = template.Library()
register.filter("money", format_money)
register.filter("number", format_number)
register.filter("percent", format_percent)
