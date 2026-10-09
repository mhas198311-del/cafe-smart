import os
import sys
from pathlib import Path

# Bulletproof sys.path resolution for Railway / cloud hosts
curr_dir = Path(__file__).resolve().parent
if str(curr_dir) not in sys.path:
    sys.path.insert(0, str(curr_dir))

sub_cafe = curr_dir / "cafe_smart"
if sub_cafe.exists() and (sub_cafe / "manage.py").exists() and str(sub_cafe) not in sys.path:
    sys.path.insert(0, str(sub_cafe))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cafe_smart.settings')

from django.core.asgi import get_asgi_application
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
