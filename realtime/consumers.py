import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from .streams.messaging_stream import MessagingStreamHandler
from .streams.notifications_stream import NotificationStreamHandler

logger = logging.getLogger(__name__)

# Maps stream name → handler class. Add new stream types here only.
_STREAM_HANDLER_CLASSES = {
    NotificationStreamHandler.stream_name: NotificationStreamHandler,
    MessagingStreamHandler.stream_name: MessagingStreamHandler,
}

# Maps channel-layer event type → (stream_name, handler_method_name).
_CHANNEL_EVENT_DISPATCH = {
    'notification.push': ('notifications', 'handle_notification_push'),
}


class PlatformConsumer(AsyncWebsocketConsumer):
    """Multiplexed WS consumer. Protocol: {"stream": "<name>", "payload": {...}}."""

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        await self.accept()
        self._handlers = {
            name: cls(self) for name, cls in _STREAM_HANDLER_CLASSES.items()
        }
        for handler in self._handlers.values():
            try:
                await handler.on_connect(user)
            except Exception:
                logger.exception(
                    'Stream %s on_connect failed for user %s',
                    handler.stream_name, user.id,
                )

    async def disconnect(self, close_code):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            return
        for handler in self._handlers.values():
            try:
                await handler.on_disconnect(user)
            except Exception:
                logger.exception(
                    'Stream %s on_disconnect failed for user %s',
                    handler.stream_name, user.id,
                )

    async def receive(self, text_data=None, bytes_data=None):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            return

        try:
            message = json.loads(text_data or '{}')
        except (json.JSONDecodeError, TypeError):
            await self.send_to_client('error', {'detail': 'Invalid JSON.'})
            return

        stream_name = message.get('stream')
        payload = message.get('payload')
        if not stream_name or not isinstance(payload, dict):
            await self.send_to_client('error', {'detail': 'Missing stream or payload.'})
            return

        handler = self._handlers.get(stream_name)
        if handler is None:
            await self.send_to_client('error', {'detail': f'Unknown stream: {stream_name}.'})
            return

        try:
            await handler.on_receive(user, payload)
        except Exception:
            logger.exception(
                'Stream %s on_receive failed for user %s',
                stream_name, user.id,
            )

    async def notification_push(self, event: dict):
        stream_name, method_name = _CHANNEL_EVENT_DISPATCH['notification.push']
        handler = getattr(self._handlers.get(stream_name), method_name, None)
        if handler:
            try:
                await handler(event)
            except Exception:
                logger.exception('notification.push handler failed')

    async def send_to_client(self, stream: str, payload: dict):
        await self.send(text_data=json.dumps({'stream': stream, 'payload': payload}))
