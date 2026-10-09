import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from cafe_smart.settings import *
except ImportError:
    for p in BASE_DIR.glob('**/settings.py'):
        if p.resolve() != Path(__file__).resolve():
            sys.path.insert(0, str(p.parent))
            sys.path.insert(0, str(p.parent.parent))
            exec(p.read_text(), globals())
            break
