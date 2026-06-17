import logging
from typing import Optional

from channels.db import database_sync_to_async

from .base import BaseStreamHandler

logger = logging.getLogger(__name__)


class MessagingStreamHandler(BaseStreamHandler):
    """
    Handles the 'messaging' WebSocket stream.

    Wire protocol (client → server, stream='messaging'):
        send_message : { "type": "send_message", "conversation_id": <int>, "body": "<str>" }
        mark_read    : { "type": "mark_read",    "conversation_id": <int> }

    Wire protocol (server → client, stream='messaging'):
        new_message  : { "type": "new_message",  "conversation_id": <int>, "message": {…} }
        message_sent : { "type": "message_sent", "message": {…} }
        marked_read  : { "type": "marked_read",  "conversation_id": <int> }
        unread_summary: { "type": "unread_summary", "conversations": [{conversation_id, unread_count}], "unread_conversations": <int> }
        error        : { "type": "error",        "detail": "<str>" }
    """

    stream_name = 'messaging'

    def _group_name(self, user_id: int) -> str:
        return f'messaging_user_{user_id}'

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_connect(self, user):
        await self.channel_layer.group_add(
            self._group_name(user.id),
            self.channel_name,
        )
        unread = await self._get_unread_counts(user.id)
        await self.send({
            'type': 'unread_summary',
            'conversations': unread,
            'unread_conversations': len(unread),
        })

    async def on_disconnect(self, user):
        await self.channel_layer.group_discard(
            self._group_name(user.id),
            self.channel_name,
        )

    # ------------------------------------------------------------------
    # Receive (client → server)
    # ------------------------------------------------------------------

    async def on_receive(self, user, data: dict):
        action = data.get('type')
        if action == 'send_message':
            await self._handle_send_message(user, data)
        elif action == 'mark_read':
            await self._handle_mark_read(user, data)
        else:
            logger.debug(
                'messaging_stream: unknown action=%s from user=%s', action, user.id
            )
            await self.send({'type': 'error', 'detail': f'Unknown action: {action}.'})

    # ------------------------------------------------------------------
    # Push (channel layer → client)
    # ------------------------------------------------------------------

    async def handle_new_message(self, event: dict):
        """Invoked by PlatformConsumer when a messaging.new_message channel event arrives."""
        await self.send({
            'type': 'new_message',
            'conversation_id': event['conversation_id'],
            'message': event['message'],
        })

    # ------------------------------------------------------------------
    # Internal action handlers
    # ------------------------------------------------------------------

    async def _handle_send_message(self, user, data: dict):
        conversation_id = data.get('conversation_id')
        body = (data.get('body') or '').strip()

        if not conversation_id or not isinstance(conversation_id, int):
            await self.send({'type': 'error', 'detail': 'conversation_id (int) is required.'})
            return
        if not body:
            await self.send({'type': 'error', 'detail': 'body must not be blank.'})
            return
        if len(body) > 5000:
            await self.send({'type': 'error', 'detail': 'body must not exceed 5000 characters.'})
            return

        result = await self._send_message_db(user, conversation_id, body)
        if result is None:
            # Logged inside the sync helper; surface a generic error to the client.
            await self.send({'type': 'error', 'detail': 'Could not send message.'})
            return

        if result.get('error'):
            await self.send({'type': 'error', 'detail': result['error']})
            return

        await self.send({'type': 'message_sent', 'message': result['message']})

    async def _handle_mark_read(self, user, data: dict):
        conversation_id = data.get('conversation_id')
        if not conversation_id or not isinstance(conversation_id, int):
            await self.send({'type': 'error', 'detail': 'conversation_id (int) is required.'})
            return

        await self._mark_read_db(user, conversation_id)
        await self.send({'type': 'marked_read', 'conversation_id': conversation_id})

    # ------------------------------------------------------------------
    # Database helpers (must run in sync context via database_sync_to_async)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _send_message_db(self, user, conversation_id: int, body: str) -> Optional[dict]:
        from messaging.models import Conversation
        from messaging.services.messaging_service import MessagingError, send_message
        try:
            message = send_message(user=user, conversation_id=conversation_id, body=body)
            return {
                'message': {
                    'id': message.pk,
                    'conversation_id': message.conversation_id,
                    'sender_id': message.sender_id,
                    'body': message.body,
                    'is_deleted': message.is_deleted,
                    'created_at': message.created_at.isoformat(),
                }
            }
        except Conversation.DoesNotExist:
            return {'error': 'Conversation not found.'}
        except MessagingError as exc:
            logger.warning(
                'messaging_stream: send blocked user=%s conv=%s reason=%s',
                user.id, conversation_id, exc.message,
            )
            return {'error': exc.message}
        except Exception:
            logger.exception(
                'messaging_stream: _send_message_db failed user=%s conv=%s',
                user.id, conversation_id,
            )
            return None

    @database_sync_to_async
    def _mark_read_db(self, user, conversation_id: int) -> None:
        from messaging.models import Conversation
        from messaging.services.messaging_service import mark_read
        try:
            mark_read(user=user, conversation_id=conversation_id)
        except Conversation.DoesNotExist:
            pass
        except Exception:
            logger.exception(
                'messaging_stream: _mark_read_db failed user=%s conv=%s',
                user.id, conversation_id,
            )

    @database_sync_to_async
    def _get_unread_counts(self, user_id: int) -> list:
        from authentication.models import User
        from messaging.services.messaging_service import get_unread_counts
        try:
            user = User.objects.get(pk=user_id)
            return get_unread_counts(user)
        except User.DoesNotExist:
            return []
        except Exception:
            logger.exception('messaging_stream: _get_unread_counts failed user=%s', user_id)
            return []
