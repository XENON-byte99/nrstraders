from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'documents'

    def ready(self):
        # Registers the "remember deletes for sync" signal in every process
        # (web server, management commands, the phone app).
        from . import live_sync  # noqa: F401
