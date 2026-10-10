from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/table/$', consumers.TableCallConsumer.as_asgi()),
    re_path(r'ws/table-calls/$', consumers.TableCallConsumer.as_asgi()),
    re_path(r'ws/staff/$', consumers.TableCallConsumer.as_asgi()),
]
