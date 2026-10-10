import random
import logging

logger = logging.getLogger(__name__)

class WhatsAppClient:
    """
    WhatsApp Gateway API (UltraMsg integration with mock fallback).
    """
    sent_messages = []

    @classmethod
    def send_via_ultramsg(cls, phone_number, message):
        try:
            from accounts.models import CafeConfiguration
            config = CafeConfiguration.objects.first()
            if config and config.ultramsg_instance_id and config.ultramsg_token:
                import requests
                # Clean phone number: keep only digits
                cleaned_phone = "".join(filter(str.isdigit, str(phone_number)))
                # If Jordanian local format, prepend 962
                if cleaned_phone.startswith("07") and len(cleaned_phone) == 10:
                    cleaned_phone = "962" + cleaned_phone[1:]
                
                url = f"https://api.ultramsg.com/{config.ultramsg_instance_id}/messages/chat"
                payload = {
                    "token": config.ultramsg_token,
                    "to": cleaned_phone,
                    "body": message
                }
                res = requests.post(url, data=payload, timeout=10)
                logger.info(f"UltraMsg API Response: {res.status_code} - {res.text}")
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Error in send_via_ultramsg: {e}")
        return False

    @classmethod
    def send_otp(cls, phone_number, otp_code):
        msg = f"Your 26 Cafe verification code is: {otp_code}. Valid for 5 minutes."
        
        is_sent = cls.send_via_ultramsg(phone_number, msg)
        status = "Sent (Real)" if is_sent else "Logged (Mock)"
        
        cls.sent_messages.append({
            "phone_number": phone_number,
            "type": "OTP",
            "message": f"Your 26 Cafe verification code is: ***. Valid for 5 minutes.",
            "status": status
        })
        
        logger.info(f"[WhatsApp OTP] {status} to {phone_number}")
        try:
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {msg}\n========================================\n")
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {safe_msg}\n========================================\n")
        return True

    @classmethod
    def send_winner_notification(cls, phone_number, full_name, ticket_code, prize_name="Premium Coffee Box"):
        msg = f"Congratulations {full_name}! 🎉 Your raffle ticket {ticket_code} has won the 26 Cafe draw for {prize_name}! Show this message to our staff to claim your prize."
        
        is_sent = cls.send_via_ultramsg(phone_number, msg)
        status = "Sent (Real)" if is_sent else "Logged (Mock)"
        
        cls.sent_messages.append({
            "phone_number": phone_number,
            "type": "WINNER",
            "message": msg,
            "status": status
        })
        
        logger.info(f"[WhatsApp Winner] {status} to {phone_number}: {msg}")
        try:
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {msg}\n========================================\n")
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {safe_msg}\n========================================\n")
        return True

    @classmethod
    def send_welcome_loyalty(cls, phone_number, full_name):
        msg = f"أهلاً بك {full_name} في نادي عملاء 26 Cafe! ☕️🎉\nتم تفعيل بطاقتك بنجاح. اجمع النقاط واستمتع بالمكافآت مع كل زيارة! 🎁"
        
        is_sent = cls.send_via_ultramsg(phone_number, msg)
        status = "Sent (Real)" if is_sent else "Logged (Mock)"
        
        cls.sent_messages.append({
            "phone_number": phone_number,
            "type": "WELCOME",
            "message": msg,
            "status": status
        })
        logger.info(f"[WhatsApp Welcome] {status} to {phone_number}: {msg}")
        try:
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {msg}\n========================================\n")
        except Exception:
            pass
        return True

    @classmethod
    def send_points_earned(cls, phone_number, points_earned, total_points):
        msg = f"شكراً لزيارتك لـ 26 Cafe! 😍☕️\nتم إضافة نقاط فاتورتك بنجاح.\nرصيدك الحالي: {total_points} نقطة. نراك قريباً! 👋"
        
        is_sent = cls.send_via_ultramsg(phone_number, msg)
        status = "Sent (Real)" if is_sent else "Logged (Mock)"
        
        cls.sent_messages.append({
            "phone_number": phone_number,
            "type": "POINTS_EARNED",
            "message": msg,
            "status": status
        })
        logger.info(f"[WhatsApp Points] {status} to {phone_number}: {msg}")
        try:
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {phone_number}\nStatus: {status}\nMessage: {msg}\n========================================\n")
        except Exception:
            pass
        return True

    @classmethod
    def send_table_alert(cls, table_number, call_type_key):
        from accounts.models import CafeConfiguration
        config = CafeConfiguration.objects.first()
        if not config or not config.whatsapp_number:
            logger.warning("No whatsapp configuration found for table alert fallback.")
            return False
        
        type_mapping = {
            'call': 'الويتر 🤵',
            'bill': 'الحساب 💳',
            'hookah': 'خدمة الأراجيل 💨'
        }
        call_name = type_mapping.get(call_type_key, 'الخدمة')
        msg = f"⚠️ *تنبيه طاولة:*\nطاولة رقم *{table_number}* تطلب *{call_name}*."
        
        is_sent = cls.send_via_ultramsg(config.whatsapp_number, msg)
        status = "Sent (Real)" if is_sent else "Logged (Mock)"
        
        cls.sent_messages.append({
            "phone_number": config.whatsapp_number,
            "type": "TABLE_ALERT",
            "message": msg,
            "status": status
        })
        logger.info(f"[WhatsApp Table Alert] {status} to {config.whatsapp_number}: {msg}")
        try:
            print(f"\n========================================\n[WHATSAPP MESSAGE SENT]\nTo: {config.whatsapp_number}\nStatus: {status}\nMessage: {msg}\n========================================\n")
        except Exception:
            pass
        return True

class LoyverseClient:
    """
    Mock Loyverse API integration.
    """
    @classmethod
    def get_receipts(cls, min_amount=0.0):
        """
        Simulate querying Loyverse API for receipts.
        Returns a list of mock receipts for testing the raffle calculator.
        """
        # Generate some mock receipts
        receipts = [
            {
                "receipt_number": "LV-1001",
                "total_amount": 15.500,
                "created_at": "2026-06-12T10:00:00Z"
            },
            {
                "receipt_number": "LV-1002",
                "total_amount": 8.750,
                "created_at": "2026-06-12T11:30:00Z"
            },
            {
                "receipt_number": "LV-1003",
                "total_amount": 25.000,
                "created_at": "2026-06-12T12:15:00Z"
            },
            {
                "receipt_number": "LV-1004",
                "total_amount": 32.200,
                "created_at": "2026-06-12T14:00:00Z"
            },
            {
                "receipt_number": "LV-1005",
                "total_amount": 5.000,
                "created_at": "2026-06-12T15:30:00Z"
            }
        ]
        return [r for r in receipts if r["total_amount"] >= min_amount]

    @classmethod
    def deduct_points(cls, loyverse_customer_id, points_to_deduct):
        """
        Simulates point deduction from Loyverse customer profile via API.
        """
        logger.info(f"[Loyverse API] Successfully deducted {points_to_deduct} points from customer {loyverse_customer_id}")
        return True
