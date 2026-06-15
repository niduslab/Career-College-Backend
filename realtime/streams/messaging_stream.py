import logging

from .base import BaseStreamHandler

logger = logging.getLogger(__name__)


class MessagingStreamHandler(BaseStreamHandler):
    """Stub — registered now so the wire protocol is stable; full impl lands with messaging feature."""

    stream_name = 'messaging'

    async def on_connect(self, user):
        pass

    async def on_disconnect(self, user):
        pass

    async def on_receive(self, user, data: dict):
        logger.debug('messaging_stream: received from user %s (not yet implemented)', user.id)
