"""
Django settings for pro_channels project.
"""
from pathlib import Path
import os
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Безопасность ─────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY', default='django-insecure-dev-key-change-me')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*', cast=Csv())

# ─── HTTPS behind reverse proxy (Nginx) ───────────────────────────────────────
# When running behind Nginx (TLS termination) Django must trust the forwarded
# scheme and origins, otherwise admin login may fail CSRF validation.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = [
    'https://prochannels.ru',
    'https://www.prochannels.ru',
]

# ─── Приложения ───────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',

    # Celery Beat
    'django_celery_beat',
    'django_celery_results',

    # Приложения проекта
    'accounts',
    'bots',
    'channels',
    'content',
    'parsing',
    'stats',
    'billing',
    'managers',
    'advertisers',
    'ord_marking',
    'core',
]

AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# ─── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.PageVisitMiddleware',
    'core.middleware.SubscriptionMiddleware',
]

ROOT_URLCONF = 'pro_channels.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'pro_channels.wsgi.application'

# ─── База данных ──────────────────────────────────────────────────────────────
# Временно SQLite для разработки. Для продакшена переключить на MySQL.
_USE_MYSQL = config('USE_MYSQL', default=False, cast=bool)
if _USE_MYSQL:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': config('DB_NAME', default='pro_channels'),
            'USER': config('DB_USER', default='root'),
            'PASSWORD': config('DB_PASSWORD', default=''),
            'HOST': config('DB_HOST', default='127.0.0.1'),
            'PORT': config('DB_PORT', default='3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db_dev.sqlite3',
        }
    }

# ─── Безопасность паролей ─────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ─── Локализация ──────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

# ─── Статика и медиа ──────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Аутентификация ───────────────────────────────────────────────────────────
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailOrUsernameBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ─── Шифрование токенов ботов ─────────────────────────────────────────────────
BOTS_ENCRYPTION_KEY = config('BOTS_ENCRYPTION_KEY', default='')
if not BOTS_ENCRYPTION_KEY and DEBUG:
    from cryptography.fernet import Fernet
    BOTS_ENCRYPTION_KEY = Fernet.generate_key().decode()

# ─── Email ────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='smtp.yandex.ru')
EMAIL_PORT = config('EMAIL_PORT', default=465, cast=int)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='ProChannels <noreply@prochannels.ru>')

# ─── Celery ───────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
CELERY_BROKER_TRANSPORT_OPTIONS = {'socket_connect_timeout': 5, 'socket_timeout': 5}
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_TIMEZONE = 'Europe/Moscow'
CELERY_TASK_TRACK_STARTED = True
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
# Базовая очередь для парсинга, beat, прочего. Публикация и импорт истории — в prio, иначе при хвосте
# из execute_parse_task посты и TG→MAX могут часами не доходить до воркера (один Redis-список «celery»).
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_TASK_ROUTES = {
    # Раз в минуту: не должна стоять в хвосте за тысячами execute_parse_task в «celery».
    'content.tasks.check_scheduled_posts': {'queue': 'prio'},
    'content.tasks.publish_post_task': {'queue': 'prio'},
    # Отдельная очередь: иначе импорт TG→MAX висит в «Ожидание воркера» за сотнями publish_post_task в prio.
    'channels.tasks.import_tg_history_to_max_task': {'queue': 'import_history'},
}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Долгие задачи (парсинг, импорт истории): не отбирать несколько сообщений в один процесс.
CELERY_WORKER_PREFETCH_MULTIPLIER = config('CELERY_WORKER_PREFETCH_MULTIPLIER', default=1, cast=int)
# True = задачи выполняются в процессе web, воркер и Redis-очередь не используются (только отладка).
CELERY_TASK_ALWAYS_EAGER = config('CELERY_TASK_ALWAYS_EAGER', default=False, cast=bool)
CELERY_TASK_EAGER_PROPAGATES = True

# Пост в «Публикуется» без завершения Celery (рестарт воркера): снова поставить publish_post_task через N минут
STUCK_PUBLISHING_RECOVER_MINUTES = config('STUCK_PUBLISHING_RECOVER_MINUTES', default=15, cast=int)

# ─── Cache ────────────────────────────────────────────────────────────────────
# Общий для web + Celery (буфер альбомов Telegram). Без Redis все воркеры не видят одни данные.
def _redis_url_select_db(url: str, db: int) -> str:
    from urllib.parse import urlparse, urlunparse

    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, f'/{db}', '', '', ''))


DJANGO_CACHE_REDIS_URL = config('DJANGO_CACHE_REDIS_URL', default='')
if not DJANGO_CACHE_REDIS_URL:
    DJANGO_CACHE_REDIS_URL = _redis_url_select_db(CELERY_BROKER_URL, 2)

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': DJANGO_CACHE_REDIS_URL,
        'KEY_PREFIX': 'pch',
        'TIMEOUT': 300,
    },
}

# ─── DeepSeek (рерайт / AI пост; OpenAI-совместимый endpoint) ────────────────
DEEPSEEK_API_KEY = config('DEEPSEEK_API_KEY', default='')
DEEPSEEK_API_BASE = config('DEEPSEEK_API_BASE', default='https://api.deepseek.com')
DEEPSEEK_MODEL = config('DEEPSEEK_MODEL', default='deepseek-chat')
AI_REWRITE_ENABLED = config('AI_REWRITE_ENABLED', default=False, cast=bool)

# ─── TBank ────────────────────────────────────────────────────────────────────
TBANK_TERMINAL_KEY = config('TBANK_TERMINAL_KEY', default='')
TBANK_SECRET_KEY = config('TBANK_SECRET_KEY', default='')
TBANK_API_URL = config('TBANK_API_URL', default='https://securepay.tinkoff.ru/v2/')

# ─── ВК ОРД ──────────────────────────────────────────────────────────────────
VK_ORD_ACCESS_TOKEN = config('VK_ORD_ACCESS_TOKEN', default='')
VK_ORD_CABINET_ID = config('VK_ORD_CABINET_ID', default='')

# ─── Telegram Парсинг (Telethon — user API) ──────────────────────────────────
TELEGRAM_API_ID = config('TELEGRAM_API_ID', default='')
TELEGRAM_API_HASH = config('TELEGRAM_API_HASH', default='')
# Сериализация доступа к файлу *.session в Redis (см. parsing.tasks._telethon_session_lock)
TELETHON_REDIS_LOCK_TTL = config('TELETHON_REDIS_LOCK_TTL', default=28800, cast=int)
TELETHON_REDIS_LOCK_WAIT = config('TELETHON_REDIS_LOCK_WAIT', default=600, cast=int)
# Сколько последних сообщений канала смотреть за один проход (дедуп по msg id в БД).
PARSE_TELEGRAM_MESSAGE_LIMIT = config('PARSE_TELEGRAM_MESSAGE_LIMIT', default=20, cast=int)

# ─── VK Парсинг ──────────────────────────────────────────────────────────────
VK_PARSE_ACCESS_TOKEN = config('VK_PARSE_ACCESS_TOKEN', default='')

# ─── Instagram Graph API ─────────────────────────────────────────────────────
INSTAGRAM_APP_ID = config('INSTAGRAM_APP_ID', default='')
INSTAGRAM_APP_SECRET = config('INSTAGRAM_APP_SECRET', default='')

# ─── Сайт ─────────────────────────────────────────────────────────────────────
SITE_URL = config('SITE_URL', default='http://127.0.0.1:8000')
SITE_NAME = config('SITE_NAME', default='ProChannels')

# ─── Логирование ──────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'bots': {'handlers': ['console'], 'level': 'DEBUG', 'propagate': False},
        'content': {'handlers': ['console'], 'level': 'DEBUG', 'propagate': False},
        'billing': {'handlers': ['console'], 'level': 'DEBUG', 'propagate': False},
    },
}
