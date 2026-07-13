import math
from django import template

register = template.Library()

def _snap(val, tolerance=0.01):
    """Absorb float/storage noise: values within 1 paisa of a whole taka
    are treated as that whole number so ROUND_UP doesn't bill 1 taka extra."""
    near = round(val)
    return float(near) if abs(val - near) < tolerance else val

@register.filter
def format_billing(value, transaction):
    """
    Format a value according to the transaction's decimal_display_mode.
    Usage: {{ amount|format_billing:transaction }}
    """
    if value is None or value == '':
        return ""
    try:
        val = float(value)
    except (ValueError, TypeError):
        return value
        
    mode = 'DEFAULT'
    if hasattr(transaction, 'decimal_display_mode'):
        mode = transaction.decimal_display_mode
    elif isinstance(transaction, str):
        mode = transaction
        
    if mode == 'ROUND_UP':
        return str(int(math.ceil(_snap(val))))
    elif mode == 'TRUNCATE':
        return str(int(math.floor(_snap(val))))
    elif mode == 'ROUND':
        return str(int(round(val)))
    else:
        # For default, use 3 decimal places which is standard across the app
        return f"{val:.3f}"

@register.filter
def format_billing_2(value, transaction):
    """ Same as above but defaults to 2 decimal places """
    if value is None or value == '':
        return ""
    try:
        val = float(value)
    except (ValueError, TypeError):
        return value
        
    mode = 'DEFAULT'
    if hasattr(transaction, 'decimal_display_mode'):
        mode = transaction.decimal_display_mode
    elif isinstance(transaction, str):
        mode = transaction
        
    if mode == 'ROUND_UP':
        return str(int(math.ceil(_snap(val))))
    elif mode == 'TRUNCATE':
        return str(int(math.floor(_snap(val))))
    elif mode == 'ROUND':
        return str(int(round(val)))
    else:
        return f"{val:.2f}"

@register.filter
def add_values(value, arg):
    """Add two numeric values together. Usage: {{ value1|add_values:value2 }}"""
    try:
        return float(value or 0) + float(arg or 0)
    except (ValueError, TypeError):
        return value
