"""
ASGI config for cafe_smart project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
import sys
from pathlib import Path

# Add project root directory to sys.path for cloud runners
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cafe_smart.settings')
django.setup()
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import accounts.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            accounts.routing.websocket_urlpatterns
        )
    ),
})
