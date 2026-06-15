from django.urls import re_path

from .consumers import PlatformConsumer

websocket_urlpatterns = [
    re_path(r'^ws/$', PlatformConsumer.as_asgi()),
]
