from django import template

register = template.Library()


@register.filter
def category_label(value):
    return str(value).replace("_", " ").capitalize()
