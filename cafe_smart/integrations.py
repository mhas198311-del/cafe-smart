import random
import logging

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v23.0"
TEMPLATE_LANGUAGE = "ar"

# Message templates the cafe sends first. Meta must approve each one before it
# can be used; "Submit templates" in Settings → WhatsApp creates them all.
TEMPLATES = {
    "otp": {
        "name": "wb_verification_code",
        "category": "AUTHENTICATION",
    },
    "welcome": {
        "name": "wb_loyalty_welcome",
        "category": "UTILITY",
        "body": "أهلاً {{1}}! تم تفعيل بطاقة الولاء الخاصة بك في White Bird Cafe. اجمع النقاط مع كل زيارة واستبدلها بمكافآت من الكاشير.",
        "example": ["محمد"],
    },
    "points": {
        "name": "wb_points_earned",
        "category": "UTILITY",
        "body": "شكراً لزيارتك White Bird Cafe! تمت إضافة {{1}} نقطة إلى حسابك، ورصيدك الآن {{2}} نقطة.",
        "example": ["25", "340"],
    },
    "winner": {
        "name": "wb_draw_winner",
        "category": "UTILITY",
        "body": "مبروك {{1}}! تذكرتك رقم {{2}} فازت في سحب White Bird Cafe بجائزة: {{3}}. اعرض هذه الرسالة على الكاشير لاستلام جائزتك.",
        "example": ["محمد", "TKT-123456", "بوكس قهوة مختصة"],
    },
    # Marketing messages: only sent to members who agreed to them
    "birthday": {
        "name": "wb_birthday_gift",
        "category": "MARKETING",
        "body": "كل عام وأنت بخير يا {{1}} 🎂 هديتك من White Bird Cafe بمناسبة عيد ميلادك: {{2}}. اعرض بطاقة العضوية على الكاشير خلال 7 أيام لاستلامها.",
        "example": ["محمد", "مشروب مجاني من اختيارك"],
    },
    "offer": {
        "name": "wb_special_offer",
        "category": "MARKETING",
        "body": "مرحباً {{1}}، عندنا عرض خاص لأعضاء White Bird Cafe: {{2}}. نستناك ☕",
        "example": ["محمد", "خصم 20% على كل المشروبات الباردة هذا الأسبوع"],
    },
}


def whatsapp_digits(phone_number):
    """Phone number as WhatsApp expects it: country code and number, digits only."""
    digits = "".join(c for c in str(phone_number or "") if c.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("07") and len(digits) == 10:
        digits = "962" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "962" + digits
    return digits


def template_definition(key):
    """The create-template request body Meta expects for one of TEMPLATES."""
    spec = TEMPLATES[key]
    if spec["category"] == "AUTHENTICATION":
        components = [
            {"type": "BODY", "add_security_recommendation": True},
            {"type": "FOOTER", "code_expiration_minutes": 5},
            {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "نسخ الرمز"}]},
        ]
    else:
        components = [{"type": "BODY", "text": spec["body"], "example": {"body_text": [spec["example"]]}}]
    return {"name": spec["name"], "language": TEMPLATE_LANGUAGE, "category": spec["category"], "components": components}


class WhatsAppNotConfigured(Exception):
    pass


class WhatsAppClient:
    """
    WhatsApp Business Platform (Meta Cloud API). Every send is logged in
    WhatsAppMessage, and Meta's delivery updates arrive on the webhook.
    """

    @staticmethod
    def _config():
        from accounts.models import CafeConfiguration
        config = CafeConfiguration.objects.first()
        if not config or not config.wa_phone_number_id or not config.wa_access_token:
            raise WhatsAppNotConfigured("WhatsApp is not connected yet (Settings → WhatsApp).")
        return config

    @classmethod
    def is_configured(cls):
        try:
            cls._config()
            return True
        except WhatsAppNotConfigured:
            return False

    @classmethod
    def graph(cls, method, path, payload=None, params=None):
        import requests
        config = cls._config()
        res = requests.request(
            method, f"{GRAPH_API}/{path}",
            headers={"Authorization": f"Bearer {config.wa_access_token}"},
            json=payload, params=params, timeout=15,
        )
        try:
            data = res.json()
        except ValueError:
            data = {}
        if res.status_code >= 400:
            error = data.get("error", {})
            raise RuntimeError(error.get("error_user_msg") or error.get("message") or f"HTTP {res.status_code}")
        return data

    @classmethod
    def _send(cls, phone_number, kind, payload, preview):
        from accounts.models import WhatsAppMessage
        to = whatsapp_digits(phone_number)
        log = WhatsAppMessage.objects.create(direction="OUT", phone=to, kind=kind, text=preview[:1000], status="pending")
        try:
            config = cls._config()
            data = cls.graph("POST", f"{config.wa_phone_number_id}/messages",
                             {"messaging_product": "whatsapp", "to": to, **payload})
            log.wa_message_id = (data.get("messages") or [{}])[0].get("id", "")
            log.status = "sent"
        except Exception as e:
            log.status = "failed"
            log.error = str(e)[:500]
            logger.warning(f"[WhatsApp] {kind} to {to} failed: {e}")
        log.save()
        return log.status == "sent"

    @classmethod
    def send_template(cls, phone_number, key, params):
        spec = TEMPLATES[key]
        params = [str(p) for p in params]
        components = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
        if spec["category"] == "AUTHENTICATION":
            components.append({"type": "button", "sub_type": "url", "index": "0",
                               "parameters": [{"type": "text", "text": params[0]}]})
            preview = "رمز التحقق: ******"
        else:
            preview = spec["body"]
            for i, p in enumerate(params, 1):
                preview = preview.replace("{{%d}}" % i, p)
        payload = {"type": "template", "template": {
            "name": spec["name"], "language": {"code": TEMPLATE_LANGUAGE}, "components": components}}
        return cls._send(phone_number, key, payload, preview)

    @classmethod
    def send_text(cls, phone_number, text):
        """Free-form reply. Meta only delivers it within 24 hours of the customer's last message."""
        return cls._send(phone_number, "reply", {"type": "text", "text": {"body": text}}, text)

    @classmethod
    def send_otp(cls, phone_number, otp_code):
        return cls.send_template(phone_number, "otp", [otp_code])

    @classmethod
    def send_welcome_loyalty(cls, phone_number, full_name):
        return cls.send_template(phone_number, "welcome", [full_name])

    @classmethod
    def send_points_earned(cls, phone_number, points_earned, total_points):
        return cls.send_template(phone_number, "points", [points_earned, total_points])

    @classmethod
    def send_winner_notification(cls, phone_number, full_name, ticket_code, prize_name="جائزة السحب"):
        return cls.send_template(phone_number, "winner", [full_name, ticket_code, prize_name])

    @classmethod
    def send_birthday_gift(cls, phone_number, full_name, gift):
        return cls.send_template(phone_number, "birthday", [full_name, gift])

    @classmethod
    def send_offer(cls, phone_number, full_name, offer_text):
        return cls.send_template(phone_number, "offer", [full_name, offer_text])

    @classmethod
    def template_statuses(cls):
        """Name → status (APPROVED, PENDING, REJECTED) of the templates on the WhatsApp account."""
        config = cls._config()
        if not config.wa_business_account_id:
            raise WhatsAppNotConfigured("WhatsApp Business Account ID is missing.")
        data = cls.graph("GET", f"{config.wa_business_account_id}/message_templates",
                         params={"fields": "name,status,category", "limit": 100})
        return {t["name"]: t.get("status", "") for t in data.get("data", [])}

    @classmethod
    def submit_templates(cls):
        """Creates any of TEMPLATES that the account doesn't have yet. Returns name → status or error."""
        config = cls._config()
        existing = cls.template_statuses()
        result = {}
        for key, spec in TEMPLATES.items():
            if spec["name"] in existing:
                result[spec["name"]] = existing[spec["name"]]
                continue
            try:
                data = cls.graph("POST", f"{config.wa_business_account_id}/message_templates", template_definition(key))
                result[spec["name"]] = data.get("status", "PENDING")
            except Exception as e:
                result[spec["name"]] = f"ERROR: {e}"
        return result


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

    @classmethod
    def ensure_receipts_webhook(cls, url):
        """Registers url as a receipts.update webhook in Loyverse (or re-enables it). Returns "created", "enabled" or "exists"."""
        hooks = (cls._request("GET", "/webhooks") or {}).get("webhooks", [])
        for hook in hooks:
            if hook.get("url") == url and hook.get("type") == "receipts.update":
                if hook.get("status") == "ENABLED":
                    return "exists"
                cls._request("POST", "/webhooks", json={"id": hook.get("id"), "url": url, "type": "receipts.update", "status": "ENABLED"})
                return "enabled"
        cls._request("POST", "/webhooks", json={"url": url, "type": "receipts.update", "status": "ENABLED"})
        return "created"

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
