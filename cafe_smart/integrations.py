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

class LoyverseError(Exception):
    pass


class LoyverseClient:
    """
    Loyverse POS API (https://api.loyverse.com/v1.0).
    Loyverse is the source of truth for customer points: the website only reads
    them, and points are redeemed by the cashier inside Loyverse.
    """
    BASE_URL = "https://api.loyverse.com/v1.0"

    @classmethod
    def get_token(cls):
        from django.conf import settings
        from accounts.models import CafeConfiguration
        config = CafeConfiguration.objects.first()
        token = (config.loyverse_api_token if config else None) or getattr(settings, "LOYVERSE_API_TOKEN", "")
        return (token or "").strip()

    @classmethod
    def is_configured(cls):
        return bool(cls.get_token())

    @classmethod
    def _request(cls, method, path, **kwargs):
        import requests
        token = cls.get_token()
        if not token:
            raise LoyverseError("Loyverse API token is not configured")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            res = requests.request(method, f"{cls.BASE_URL}{path}", headers=headers, timeout=10, **kwargs)
        except requests.RequestException as e:
            raise LoyverseError(f"Loyverse connection failed: {e}")
        if res.status_code == 404:
            return None
        if res.status_code >= 400:
            raise LoyverseError(f"Loyverse API {method} {path} returned {res.status_code}: {res.text[:200]}")
        return res.json() if res.content else {}

    @staticmethod
    def _phone_key(phone):
        """Last 9 digits, so +962791112233 and 0791112233 match."""
        digits = "".join(c for c in str(phone or "") if c.isdigit())
        return digits[-9:] if len(digits) >= 9 else digits

    @classmethod
    def get_customer(cls, customer_id):
        return cls._request("GET", f"/customers/{customer_id}")

    @classmethod
    def find_customer_by_phone(cls, phone_number, max_pages=20):
        """Loyverse has no phone filter, so page through customers (250 per page)."""
        wanted = cls._phone_key(phone_number)
        if not wanted:
            return None
        cursor = None
        for _ in range(max_pages):
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            data = cls._request("GET", "/customers", params=params) or {}
            for c in data.get("customers", []):
                if c.get("deleted_at"):
                    continue
                if cls._phone_key(c.get("phone_number")) == wanted:
                    return c
            cursor = data.get("cursor")
            if not cursor:
                break
        return None

    @classmethod
    def save_customer(cls, payload):
        """Creates a customer, or updates it when payload contains "id"."""
        return cls._request("POST", "/customers", json=payload)

    @classmethod
    def get_receipt(cls, receipt_number):
        return cls._request("GET", f"/receipts/{receipt_number}")

    @classmethod
    def link_customer(cls, customer):
        """
        Makes sure the website customer exists in Loyverse and stores its id.
        Reuses an existing Loyverse customer with the same phone number.
        Returns the Loyverse customer dict.
        """
        lv = cls.find_customer_by_phone(customer.phone_number)
        if lv:
            if not lv.get("customer_code"):
                lv = cls.save_customer({
                    "id": lv["id"],
                    "name": lv.get("name") or customer.full_name,
                    "customer_code": customer.barcode,
                }) or lv
        else:
            lv = cls.save_customer({
                "name": customer.full_name,
                "phone_number": customer.phone_number,
                "customer_code": customer.barcode,
                "note": "Registered on the website",
            })
        if not lv or not lv.get("id"):
            raise LoyverseError("Loyverse did not return a customer id")
        customer.loyverse_customer_id = lv["id"]
        if lv.get("customer_code"):
            customer.barcode = lv["customer_code"]
        customer.points_balance = int(float(lv.get("total_points") or 0))
        from django.utils import timezone
        customer.points_synced_at = timezone.now()
        customer.save(update_fields=["loyverse_customer_id", "barcode", "points_balance", "points_synced_at"])
        return lv

    @classmethod
    def refresh_customer_points(cls, customer, max_age_seconds=60):
        """
        Updates customer.points_balance from Loyverse (at most once a minute).
        Links the customer first if registration happened while Loyverse was unreachable.
        Returns True when the shown balance is fresh from Loyverse.
        """
        from django.utils import timezone
        if not cls.is_configured():
            return False
        if customer.points_synced_at and (timezone.now() - customer.points_synced_at).total_seconds() < max_age_seconds:
            return True
        try:
            if customer.loyverse_customer_id.startswith("PENDING-"):
                cls.link_customer(customer)
                return True
            lv = cls.get_customer(customer.loyverse_customer_id)
            if not lv:
                return False
            customer.points_balance = int(float(lv.get("total_points") or 0))
            customer.points_synced_at = timezone.now()
            customer.save(update_fields=["points_balance", "points_synced_at"])
            return True
        except LoyverseError as e:
            logger.warning(f"Loyverse points refresh failed for customer {customer.id}: {e}")
            return False
