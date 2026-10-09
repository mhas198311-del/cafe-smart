#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

def find_settings_module():
    try:
        import cafe_smart.settings
        return 'cafe_smart.settings'
    except ImportError:
        pass

    for p in BASE_DIR.glob('**/settings.py'):
        parts = p.relative_to(BASE_DIR).parts
        if len(parts) >= 2:
            pkg = parts[-2]
            mod_path = f"{pkg}.settings"
            parent_dir = str(p.parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            try:
                __import__(mod_path)
                return mod_path
            except ImportError:
                pass
    return 'cafe_smart.settings'

def main():
    """Run administrative tasks."""
    settings_mod = find_settings_module()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_mod)

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
