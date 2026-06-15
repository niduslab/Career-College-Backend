import logging

from channels.db import database_sync_to_async

from .base import BaseStreamHandler

logger = logging.getLogger(__name__)


class NotificationStreamHandler(BaseStreamHandler):
    stream_name = 'notifications'

    def _group_name(self, user_id: int) -> str:
        return f'notifications_user_{user_id}'

    async def on_connect(self, user):
        await self.channel_layer.group_add(
            self._group_name(user.id),
            self.channel_name,
        )
        unread = await self._get_unread_count(user.id)
        await self.send({'type': 'unread_count', 'count': unread})

    async def on_disconnect(self, user):
        await self.channel_layer.group_discard(
            self._group_name(user.id),
            self.channel_name,
        )

    async def on_receive(self, user, data: dict):
        action = data.get('type')
        if action == 'mark_read':
            ids = data.get('ids', [])
            if ids:
                await self._mark_ids_read(user.id, ids)
                unread = await self._get_unread_count(user.id)
                await self.send({'type': 'unread_count', 'count': unread})
        elif action == 'mark_all_read':
            await self._mark_all_read(user.id)
            await self.send({'type': 'unread_count', 'count': 0})
        else:
            logger.debug('notifications_stream: unknown action %s from user %s', action, user.id)

    async def handle_notification_push(self, event: dict):
        await self.send({
            'type': 'notification',
            'notification': event['notification'],
        })
        unread = await self._get_unread_count(event['notification']['recipient_id'])
        await self.send({'type': 'unread_count', 'count': unread})

    @database_sync_to_async
    def _get_unread_count(self, user_id: int) -> int:
        from notifications.models import Notification
        return Notification.objects.filter(recipient_id=user_id, is_read=False).count()

    @database_sync_to_async
    def _mark_ids_read(self, user_id: int, ids: list):
        from django.utils import timezone
        from notifications.models import Notification
        Notification.objects.filter(
            recipient_id=user_id, id__in=ids, is_read=False
        ).update(is_read=True, read_at=timezone.now())

    @database_sync_to_async
    def _mark_all_read(self, user_id: int):
        from django.utils import timezone
        from notifications.models import Notification
        Notification.objects.filter(
            recipient_id=user_id, is_read=False
        ).update(is_read=True, read_at=timezone.now())
