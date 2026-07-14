from django.apps import AppConfig


class AdminConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'admin_console'
    verbose_name = 'Admin Console'

    def ready(self):
        import admin_console.signals  # noqa: F401
