import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

for sub in BASE_DIR.iterdir():
    if sub.is_dir() and str(sub) not in sys.path:
        sys.path.insert(0, str(sub))

# Import settings dynamically
try:
    from cafe_smart.settings import *
except ImportError:
    for p in BASE_DIR.glob('**/settings.py'):
        if p.resolve() != Path(__file__).resolve():
            sys.path.insert(0, str(p.parent))
            exec(p.read_text(), globals())
            break

if 'DATABASES' not in globals() or not DATABASES:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
