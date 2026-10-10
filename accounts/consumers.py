import json
import datetime
import logging
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
        # Leave group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from WebSocket
    async def receive(self, text_data):
        data = json.loads(text_data)
        msg_type = data.get('type')

        if msg_type in ['call_waiter', 'request_bill', 'request_hookah']:
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

            await self.channel_layer.group_send(
                'staff_dashboard',
                {
                    'type': 'broadcast_table_call',
                    'table': self.table_number,
                    'call_type': call_type,
                    'time': now_str
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
        elif msg_type == 'accept_call':
            # Staff accepted a call. Notify the table client.
            table_num = data.get('table')
            waiter_name = data.get('waiter_name', 'Waiter')
            
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
            'time': event['time']
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

