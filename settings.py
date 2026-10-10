import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

inner_cafe = BASE_DIR / "cafe_smart"
if inner_cafe.exists() and str(inner_cafe) not in sys.path:
    sys.path.insert(0, str(inner_cafe))

from cafe_smart.settings import *
