import json
import re
import datetime
import logging
from django.core.cache import cache
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)

@database_sync_to_async
def trigger_staff_web_push(table_num, call_type):
    from .push_notifications import send_web_push_to_staff
    if call_type == 'bill':
        title = f"📄 طلب فاتورة — طاولة {table_num}"
        body = f"طاولة رقم {table_num} تطلب الحساب والفاتورة الآن"
    elif call_type == 'hookah':
        title = f"💨 طلب أرجيلة — طاولة {table_num}"
        body = f"طاولة رقم {table_num} تطلب خدمة الأرجيلة والفحم"
    else:
        title = f"🚨 نداء نادل — طاولة {table_num}"
        body = f"طاولة رقم {table_num} تنادي الموظف / النادل للخدمة"
    send_web_push_to_staff(title=title, body=body, url="/staff/app/", tag=f"table-{table_num}")

TABLE_NUMBER_RE = re.compile(r'^[A-Za-z0-9_-]{1,10}$')
# Seconds before the same table can send the same kind of call again
TABLE_CALL_COOLDOWN = 20


@database_sync_to_async
def log_table_call(table_num, call_type):
    from .models import TableCall
    return TableCall.objects.create(table_number=table_num, call_type=call_type).id


@database_sync_to_async
def session_staff_user_id(scope):
    session = scope.get('session')
    return session.get('staff_user_id') if session is not None else None


class TableCallConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.client_type = self.scope['query_string'].decode('utf-8')
        # Simple query string parsing: e.g. "type=staff" or "type=table&number=4"
        self.params = {}
        if self.client_type:
            parts = self.client_type.split('&')
            for part in parts:
                if '=' in part:
                    k, v = part.split('=', 1)
                    self.params[k] = v

        self.role = self.params.get('role', 'table')
        self.table_number = self.params.get('table', 'unknown')

        # Only logged-in staff may listen to all table calls; tables need a sane number
        if self.role == 'staff':
            if not await session_staff_user_id(self.scope):
                await self.close()
                return
        elif not TABLE_NUMBER_RE.match(self.table_number):
            await self.close()
            return

        # Connect staff to staff group, tables to table-specific groups
        if self.role == 'staff':
            self.group_name = 'staff_dashboard'
        else:
            self.group_name = f'table_{self.table_number}'

        # Join group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        if not hasattr(self, 'group_name'):
            return
        # Leave group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from WebSocket
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        msg_type = data.get('type')

        if msg_type in ['call_waiter', 'request_bill', 'request_hookah'] and self.role != 'staff':
            # Each call can trigger a paid WhatsApp alert: ignore repeats from the same table
            if not cache.add(f"tablecall:{self.table_number}:{msg_type}", 1, TABLE_CALL_COOLDOWN):
                return
            # Broadcast table call to staff dashboard
            now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
            if msg_type == 'call_waiter':
                call_type = 'call'
            elif msg_type == 'request_bill':
                call_type = 'bill'
            else:
                call_type = 'hookah'
            
            try:
                from .views import LIVE_TABLE_CALLS
                LIVE_TABLE_CALLS[str(self.table_number)] = call_type
            except Exception:
                pass

            call_id = await log_table_call(self.table_number, call_type)

            await self.channel_layer.group_send(
                'staff_dashboard',
                {
                    'type': 'broadcast_table_call',
                    'table': self.table_number,
                    'call_type': call_type,
                    'time': now_str,
                    'call_id': call_id,
                }
            )

            # Trigger Web Push Notification to staff phones in background
            try:
                await trigger_staff_web_push(self.table_number, call_type)
            except Exception as e:
                logger.error(f"Failed to trigger web push notification: {e}")

            # Trigger WhatsApp group alert fallback immediately in background thread
            try:
                from asgiref.sync import sync_to_async
                from cafe_smart.integrations import WhatsAppClient
                await sync_to_async(WhatsAppClient.send_table_alert, thread_sensitive=False)(self.table_number, call_type)
            except Exception as e:
                logger.error(f"Failed to trigger fallback WhatsApp table alert: {e}")
        elif msg_type == 'accept_call' and self.role == 'staff':
            # Staff accepted a call. Notify the table client.
            table_num = str(data.get('table', ''))
            waiter_name = str(data.get('waiter_name', 'Waiter'))[:60]
            if not TABLE_NUMBER_RE.match(table_num):
                return
            
            try:
                from .views import LIVE_TABLE_CALLS
                LIVE_TABLE_CALLS.pop(str(table_num), None)
            except Exception:
                pass

            await self.channel_layer.group_send(
                f'table_{table_num}',
                {
                    'type': 'broadcast_call_accepted',
                    'table': table_num,
                    'waiter_name': waiter_name
                }
            )

    # Handlers for group broadcasts
    async def broadcast_table_call(self, event):
        # Send message to Staff Dashboard
        await self.send(text_data=json.dumps({
            'event': 'new_call',
            'table': event['table'],
            'call_type': event['call_type'],
            'time': event['time'],
            'call_id': event.get('call_id'),
        }))

    async def broadcast_call_taken(self, event):
        # Another waiter took a call: other staff phones drop it
        await self.send(text_data=json.dumps({
            'event': 'call_taken',
            'call_id': event['call_id'],
            'table': event['table'],
            'waiter_name': event['waiter_name'],
            'staff_id': event['staff_id'],
        }))

    async def broadcast_call_accepted(self, event):
        # Send message to Table Client
        await self.send(text_data=json.dumps({
            'event': 'call_accepted',
            'table': event['table'],
            'waiter_name': event['waiter_name']
        }))

    async def broadcast_contact_message(self, event):
        # Send message to Staff Dashboard
        await self.send(text_data=json.dumps({
            'event': 'contact_message',
            'message': event['message']
        }))

