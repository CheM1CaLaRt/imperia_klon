from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # 1) Зарегистрировать модели заявок (лежат в отдельном модуле)
        from . import models_requests  # noqa: F401

        # 2) Подключить ВСЕ сигналы из единого файла core/signals.py
        #    (внутри него уже и твои сигналы профиля/документов, и сигналы заявок)
        from . import signals  # noqa: F401
