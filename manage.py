#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import types
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

inner_cafe = BASE_DIR / "cafe_smart"
if inner_cafe.exists():
    if str(inner_cafe) not in sys.path:
        sys.path.insert(0, str(inner_cafe))
    mod = types.ModuleType('cafe_smart')
    mod.__path__ = [str(inner_cafe), str(BASE_DIR)]
    sys.modules['cafe_smart'] = mod

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cafe_smart.settings')

def main():
    """Run administrative tasks."""
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
