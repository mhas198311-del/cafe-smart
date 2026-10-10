import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

cafe_smart_dir = BASE_DIR / "cafe_smart"
if cafe_smart_dir.exists() and str(cafe_smart_dir) not in sys.path:
    sys.path.insert(0, str(cafe_smart_dir))

try:
    from cafe_smart.settings import *
except ImportError:
    pass

# Explicit DATABASES configuration fallback for cloud runners
if 'DATABASES' not in globals() or not DATABASES:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
