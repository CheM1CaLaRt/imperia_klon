# imperia/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
BASE_DIR = Path(__file__).resolve().parent.parent

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


STATICFILES_DIRS = [BASE_DIR / "static"]  # если этой записи нет
STATIC_ROOT = BASE_DIR / "staticfiles"  # для collectstatic в production


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-замените-на-свой"

DEBUG = True

ALLOWED_HOSTS = []


INSTALLED_APPS = [
    "jazzmin",                 # ← ДОЛЖЕН быть выше admin
    'django.contrib.humanize',
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "core.apps.CoreConfig",
    "django.contrib.staticfiles",
    'rest_framework',

]
# === Jazzmin: внешний вид и меню ===
JAZZMIN_SETTINGS = {
    "site_title": "KlonMone Admin",
    "site_header": "Imperia • Панель управления",
    "site_brand": "Imperia",
    "welcome_sign": "Добро пожаловать в DarkSide",
    "show_sidebar": True,
    "navigation_expanded": True,

    # Иконки для моделей (app_label.ModelName)
    "icons": {
        "auth.User": "fas fa-user",
        "auth.Group": "fas fa-users",
        "core.SitePolicy": "fas fa-shield-alt",
    },

    # Верхнее меню: можно указывать name+url (имя URL из urls.py или внешний линк)
    "topmenu_links": [
        {"name": "Админ-главная", "url": "admin:index"},
        {"name": "Склад", "url": "warehouse_dashboard"},
        {"name": "Оператор", "url": "operator_dashboard"},
        {"name": "Менеджер", "url": "manager_dashboard"},
        {"name": "Управляющий", "url": "director_dashboard"},
        # Пример внешней ссылки:
        # {"name": "Docs", "url": "https://docs.example.com", "new_window": True},
    ],

    # Доп. улучшения
    "related_modal_active": True,   # редактирование связанных объектов в модалке
}

JAZZMIN_UI_TWEAKS = {
    "theme": "cosmo",      # варианты: cosmo, lumen, etc.
    "navbar": "navbar-dark",
    "sidebar_fixed": True,
    "actions_sticky_top": True,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "imperia.urls"

FORM_RENDERER = "django.forms.renderers.DjangoTemplates"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.user_profile",  # ← добавить
                "core.context_processors.nav_flags",
                "django.template.context_processors.csrf",     # ← добавь ЭТО
            ],
        },
    },
]

WSGI_APPLICATION = "imperia.wsgi.application"

# 💾 БАЗА ДАННЫХ (SQLite)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "imperia"),
        "USER": os.getenv("DB_USER", "imperia_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# Пароли
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 🚪 Настройки логина/логаута
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "post_login_router"
LOGOUT_REDIRECT_URL = "login"

X_FRAME_OPTIONS = "SAMEORIGIN"
