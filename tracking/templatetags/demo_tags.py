from django import template

from ..demo.protection import is_protected

register = template.Library()


@register.filter
def demo_protected(obj):
    """True when ``obj`` is protected demo seed data (always False outside demo mode)."""
    return is_protected(obj)
