import logging
from .models import AuditLog

logger = logging.getLogger(__name__)

def log_audit(user, action, details="", transaction=None):
    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action=action,
            details=details,
            transaction=transaction
        )
    except Exception as e:
        logger.error(f"[AuditLog] Failed to write log: {e} | action={action} | details={details}")
