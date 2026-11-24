"""Owner-controlled switches, changed from the app's Setup page.

Stored in the database so they take effect immediately, without editing
.env or restarting. .env values are only the starting defaults.
"""
from django.conf import settings

from .models import AppSetting


def assistant_on():
    """The master switch: the AI assistant only works while this is on."""
    return bool(AppSetting.get('assistant_on', True))


def autopilot_on():
    return bool(AppSetting.get('autopilot_on', getattr(settings, 'AUTOPILOT_ENABLED', False)))


def max_total():
    return float(AppSetting.get('autopilot_max_total', getattr(settings, 'AUTOPILOT_MAX_TOTAL', 50000)))


def trusted_after():
    return int(AppSetting.get('autopilot_trusted_after', getattr(settings, 'AUTOPILOT_TRUSTED_AFTER', 3)))


def quiet_hours():
    """(start, end) hour, or None for no quiet hours."""
    value = AppSetting.get('quiet_hours', list(getattr(settings, 'QUIET_HOURS', ()) or []))
    return tuple(value) if value and len(value) == 2 else None


def pause_on_staff_reply():
    return bool(AppSetting.get('pause_on_staff_reply', True))
