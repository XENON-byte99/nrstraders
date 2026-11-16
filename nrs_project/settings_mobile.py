"""Settings for the offline mobile app.

Same project, same code - but everything lives on the device: SQLite database,
uploads and captured messages sit in the app's private data folder
(``NRS_DATA_DIR``), and no asset is fetched from the internet.
"""
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

from .settings import *  # noqa: F401,F403
from .settings import MIDDLEWARE as _BASE_MIDDLEWARE

MOBILE_APP = True

DATA_DIR = Path(os.environ.get('NRS_DATA_DIR') or (BASE_DIR / 'mobile_data'))  # noqa: F405
DATA_DIR.mkdir(parents=True, exist_ok=True)

# A secret generated on first launch and kept with the data.
_secret_file = DATA_DIR / 'secret.key'
if not _secret_file.exists():
    _secret_file.write_text(get_random_secret_key())
SECRET_KEY = _secret_file.read_text().strip()

DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
CSRF_TRUSTED_ORIGINS = []

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(DATA_DIR / 'db.sqlite3'),
        'OPTIONS': {'timeout': 20},
    }
}
SUPABASE_DATABASE_URL = None

MEDIA_ROOT = DATA_DIR / 'media'
INBOX_SPOOL_DIR = str(DATA_DIR / 'inbox_spool')

# Static files straight from the source folders: no collectstatic step and no
# hashed-manifest lookups on the device.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
STATIC_URL = '/static/'
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = False

MIDDLEWARE = list(_BASE_MIDDLEWARE) + [
    'documents.mobile_views.FirstRunMiddleware',
    'nrs_project.offline_assets.OfflineAssetsMiddleware',
]

# It is a personal device: stay signed in.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365
SESSION_SAVE_EVERY_REQUEST = False
