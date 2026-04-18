from django import template

register = template.Library()

import builtins

@register.filter
def getattr(obj, attr):
    return builtins.getattr(obj, attr, False)
