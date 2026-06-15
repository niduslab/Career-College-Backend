"""
ASGI config for career_college_backend project.
"""

import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'career_college_backend.settings')

# Must initialise Django before importing anything that touches models/settings.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from realtime.middleware import JWTAuthMiddlewareStack  # noqa: E402
from realtime.routing import websocket_urlpatterns  # noqa: E402

_ws_app = JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))


if not settings.DEBUG:
    _ws_app = AllowedHostsOriginValidator(_ws_app)

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': _ws_app,
})
