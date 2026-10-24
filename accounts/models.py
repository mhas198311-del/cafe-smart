from django.db import models

class StaffUser(models.Model):
    ROLE_CHOICES = [('ADMIN', 'Owner/Manager'), ('WAITER', 'Floor Staff')]
    username = models.CharField(max_length=150, unique=True)
    password_hash = models.CharField(max_length=255, help_text="Argon2 or BCrypt hash")
    employee_name = models.CharField(max_length=255)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='WAITER')
    phone_number = models.CharField(max_length=20, blank=True, default="", help_text="WhatsApp number for password recovery")
    can_approve_network = models.BooleanField(default=False, help_text="May confirm the cafe Wi-Fi from the staff app when the provider changes its address")
    is_active = models.BooleanField(default=True)
    last_login = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee_name} ({self.role})"

class CustomerProfile(models.Model):
    loyverse_customer_id = models.CharField(max_length=255, unique=True, help_text="Loyverse Native Customer ID")
    phone_number = models.CharField(max_length=20, unique=True, help_text="E.164 formatted number used as unique login ID")
    password_hash = models.CharField(max_length=255)
    full_name = models.CharField(max_length=255)
    birth_date = models.DateField(help_text="Required for age +18 compliance and Birthday automated marketing campaigns")
    is_verified = models.BooleanField(default=False, help_text="True only after successful WhatsApp OTP validation")
    points_balance = models.IntegerField(default=0, help_text="Synced Loyverse points balance")
    monthly_spend = models.FloatField(default=0.0, help_text="Calculated monthly spend for tier status")
    barcode = models.CharField(max_length=100, blank=True, null=True, db_index=True, help_text="Loyverse customer barcode for scanning")
    points_synced_at = models.DateTimeField(blank=True, null=True, help_text="Last time points_balance was read from Loyverse")
    marketing_opt_in = models.BooleanField(default=False, help_text="Agreed to birthday and offer messages on WhatsApp")
    birthday_sent_year = models.PositiveIntegerField(blank=True, null=True, help_text="Year the last birthday message was sent")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name

class CafeConfiguration(models.Model):
    cafe_name = models.CharField(max_length=255, default="White Bird Cafe", help_text="The name of the cafe displayed on the website")
    phone_number = models.CharField(max_length=50, default="+962 79 123 4567")
    whatsapp_number = models.CharField(max_length=50, default="962791234567", help_text="WhatsApp format without + or spaces, e.g. 962791234567")
    email = models.EmailField(default="", blank=True)
    location_desc_en = models.CharField(max_length=255, default="Ma'an, Jordan")
    location_desc_ar = models.CharField(max_length=255, default="معان، الأردن")
    latitude = models.FloatField(default=30.1962)
    longitude = models.FloatField(default=35.7341)
    opening_hours_en = models.CharField(max_length=255, default="Daily: 7:30 AM - 11:30 PM")
    opening_hours_ar = models.CharField(max_length=255, default="يومياً: 7:30 صباحاً - 11:30 مساءً")
    instagram_url = models.URLField(default="", blank=True, null=True)
    facebook_url = models.URLField(default="", blank=True, null=True)
    tiktok_url = models.URLField(default="", blank=True, null=True)
    
    # WhatsApp Business Platform (Meta Cloud API)
    wa_phone_number_id = models.CharField(max_length=50, blank=True, default="", help_text="Phone number ID from WhatsApp → API Setup")
    wa_business_account_id = models.CharField(max_length=50, blank=True, default="", help_text="WhatsApp Business Account ID")
    wa_access_token = models.TextField(blank=True, default="", help_text="Permanent system user token")
    wa_app_secret = models.CharField(max_length=100, blank=True, default="", help_text="App secret, used to check that webhook calls come from Meta")
    wa_verify_token = models.CharField(max_length=64, blank=True, default="", help_text="Webhook verify token entered in the Meta app")

    # UltraMsg WhatsApp Credentials (no longer used)
    ultramsg_instance_id = models.CharField(max_length=50, blank=True, null=True, help_text="UltraMsg Instance ID (e.g. instance12345)")
    ultramsg_token = models.CharField(max_length=100, blank=True, null=True, help_text="UltraMsg API Token")

    # Loyverse POS API Credentials
    loyverse_api_token = models.CharField(max_length=255, blank=True, null=True, help_text="Loyverse POS Access Token")

    # Promo Video Files & Menu Cover
    promo_video_desktop = models.FileField(upload_to='videos/', blank=True, null=True, help_text="Desktop Cinematic Video File")
    promo_video_mobile = models.FileField(upload_to='videos/', blank=True, null=True, help_text="Mobile Vertical Video File")
    menu_cover_image = models.FileField(upload_to='covers/', blank=True, null=True, help_text="صورة غلاف بوابة المنيو")

    # Hero Main Titles & Slogans
    hero_title_ar = models.CharField(max_length=255, default="وايت بيرد كافيه", help_text="العنوان الرئيسي في الصفحة الرئيسية")
    hero_title_en = models.CharField(max_length=255, default="White Bird Cafe", help_text="Main Hero Title")
    hero_subtitle_ar = models.CharField(max_length=255, default="تجربة استثنائية من القهوة المختصة والمشروبات الساخنة والباردة", help_text="الوصف الترويجي الافتتاحي")
    hero_subtitle_en = models.CharField(max_length=255, default="An exceptional specialty coffee & drinks experience", help_text="Hero Subtitle")

    # Guest WiFi Settings
    wifi_ssid = models.CharField(max_length=100, default="WhiteBird_Guest", blank=True, null=True, help_text="اسم شبكة واي فاي الكافيه للزوار")
    wifi_password = models.CharField(max_length=100, default="whitebird2026", blank=True, null=True, help_text="كلمة سر واي فاي الكافيه")

    # Map Image (static screenshot of the location)
    map_image = models.FileField(upload_to='maps/', blank=True, null=True, help_text="صورة ثابتة لخريطة موقع الكافيه (Screenshot)")

    # Loyalty settings
    points_conversion_rate = models.FloatField(default=100.0, help_text="Points per 1 JOD discount value")
    raffle_ticket_threshold = models.FloatField(default=5.0, help_text="Minimum JOD spent per raffle ticket")
    raffle_prize_name = models.CharField(max_length=255, default="Premium Coffee Box")
    raffle_prize_desc = models.TextField(blank=True, null=True)
    raffle_prize_image = models.FileField(upload_to='prizes/', blank=True, null=True, help_text="Raffle Prize Image File")
    raffle_end_date = models.DateTimeField(blank=True, null=True)

    # Tier Thresholds (in Points)
    silver_tier_threshold = models.FloatField(default=500.0, help_text="Total points earned in last 12 months for Silver Tier")
    gold_tier_threshold = models.FloatField(default=1500.0, help_text="Total points earned in last 12 months for Gold Tier")

    # Birthday messages by tier
    birthday_msg_bronze = models.TextField(default="خصم 15% على طلبك اليوم")
    birthday_msg_silver = models.TextField(default="مشروب مجاني من اختيارك")
    birthday_msg_gold = models.TextField(default="مشروب وحلوى مجاناً")
    birthday_messages_enabled = models.BooleanField(default=False, help_text="Send the birthday gift on WhatsApp each morning (needs the approved birthday template)")
    birthday_last_run = models.DateField(blank=True, null=True)
    wa_marketing_price = models.DecimalField(max_digits=6, decimal_places=4, default=0, help_text="Meta's price per marketing message in USD, for cost estimates")

    # New Item Launch Promotion (Cinematic video & Special Offer)
    featured_item_is_active = models.BooleanField(default=False, help_text="Toggle to display a special featured item launch card on the menu")
    arrival_qr_required = models.BooleanField(default=False, help_text="Waiters confirm arrival by scanning the table's QR code")
    table_qr_secret = models.CharField(max_length=64, blank=True, default="", help_text="Secret in the table QR codes; change it to retire all printed codes")
    table_count = models.PositiveIntegerField(default=20, help_text="Number of tables to print QR codes for")
    table_halls = models.JSONField(default=list, blank=True, help_text="Halls, each with a letter code: [{code, name, start, count}]; tables are named like F15")
    table_card_template = models.FileField(upload_to='tables/', blank=True, null=True, help_text="Background design (e.g. from Canva) the table QR codes are printed on")
    table_card_layout = models.JSONField(default=dict, blank=True, help_text="Where the QR code and table number sit on the template, in percent of the card")
    table_calls_wifi_only = models.BooleanField(default=True, help_text="Table calls only from the cafe Wi-Fi")
    hookah_staff = models.ForeignKey("StaffUser", on_delete=models.SET_NULL, blank=True, null=True, related_name="+", help_text="Gets the hookah call notifications")
    cafe_network_ips = models.TextField(blank=True, default="", help_text="Public internet addresses of the cafe Wi-Fi, one per line. Shifts can only start or end from these.")
    cafe_network_previous = models.TextField(blank=True, default="", help_text="cafe_network_ips before the last change, for undo")
    cafe_network_changed_by = models.CharField(max_length=255, blank=True, default="")
    cafe_network_changed_at = models.DateTimeField(blank=True, null=True)
    featured_item_name = models.CharField(max_length=255, blank=True, null=True, default="Saffron Latte / لاتيه الزعفران")
    featured_item_desc = models.TextField(blank=True, null=True, default="جرب صنفنا الجديد المميز بنكهة الزعفران الفاخرة الممزوجة مع الإسبريسو العضوي.")
    featured_item_video = models.FileField(upload_to='videos/', blank=True, null=True, help_text="Promo video file for the new item")
    featured_item_special_price = models.DecimalField(max_digits=10, decimal_places=3, blank=True, null=True, help_text="Discounted price for trying the new item")

    def table_groups(self):
        """Tables grouped by hall, e.g. [{"name": "الصالة الأمامية", "code": "F", "tables": ["F1", ..., "F20"]}].
        Without halls, plain numbers 1..table_count."""
        groups = []
        for hall in self.table_halls or []:
            try:
                start, count = int(hall.get("start") or 1), int(hall.get("count") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if hall.get("code") and count > 0:
                groups.append({"name": hall.get("name", ""), "code": hall["code"],
                               "tables": [f"{hall['code']}{n}" for n in range(start, start + count)]})
        return groups or [{"name": "", "code": "", "tables": [str(n) for n in range(1, self.table_count + 1)]}]

    def __str__(self):
        return "Cafe Configuration"

    class Meta:
        verbose_name = "Cafe Configuration"
        verbose_name_plural = "Cafe Configuration"

class ContactMessage(models.Model):
    name = models.CharField(max_length=255)
    contact = models.CharField(max_length=255, help_text="Email or Phone number")
    inquiry_type = models.CharField(max_length=100)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} - {self.inquiry_type} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"

class MonthlyDraw(models.Model):
    """One draw per month: members' receipts are the entries, three places with their own prizes."""
    month = models.DateField(unique=True, help_text="First day of the month")
    prize1_name = models.CharField(max_length=255, blank=True, default="")
    prize1_image = models.CharField(max_length=500, blank=True, default="")
    prize2_name = models.CharField(max_length=255, blank=True, default="")
    prize2_image = models.CharField(max_length=500, blank=True, default="")
    prize3_name = models.CharField(max_length=255, blank=True, default="")
    prize3_image = models.CharField(max_length=500, blank=True, default="")
    drawn_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-month"]

    def prizes(self):
        return [(1, self.prize1_name, self.prize1_image), (2, self.prize2_name, self.prize2_image), (3, self.prize3_name, self.prize3_image)]

    def __str__(self):
        return f"Draw {self.month:%Y-%m}"


class RaffleTicket(models.Model):
    ticket_code = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name='raffle_tickets')
    receipt_number = models.CharField(max_length=100)
    receipt_amount = models.FloatField()
    draw_status = models.CharField(max_length=20, default='PENDING', help_text="PENDING, WON, or LOST")
    draw = models.ForeignKey(MonthlyDraw, on_delete=models.SET_NULL, blank=True, null=True, related_name="winners")
    place = models.PositiveSmallIntegerField(blank=True, null=True, help_text="1, 2 or 3 for a winning ticket")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Ticket {self.ticket_code} ({self.customer.full_name})"

class CustomerVoucher(models.Model):
    voucher_code = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name='vouchers')
    voucher_type = models.CharField(max_length=20, help_text="GIFT or BIRTHDAY")
    reward_details = models.CharField(max_length=255)
    is_redeemed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Voucher {self.voucher_code} - {self.voucher_type} ({self.customer.full_name})"

class PointsTransaction(models.Model):
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name='points_transactions')
    points = models.IntegerField(help_text="Points earned (positive) or redeemed (negative)")
    transaction_type = models.CharField(max_length=20, default='EARNED', help_text="EARNED, REDEEMED, or BIRTHDAY")
    receipt_number = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.customer.full_name} - {self.points} ({self.transaction_type})"


class StaffPushSubscription(models.Model):
    staff_user = models.ForeignKey(StaffUser, on_delete=models.CASCADE, related_name='push_subscriptions', null=True, blank=True)
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Push sub for {self.staff_user} ({self.endpoint[:30]}...)"



class LoyaltyReward(models.Model):
    """
    A reward shown to customers on the loyalty page. Customers ask the cashier for it,
    and the cashier redeems the points inside Loyverse. The website never deducts points.
    """
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True, default="")
    points_required = models.PositiveIntegerField()
    image = models.FileField(upload_to='rewards/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    order_index = models.IntegerField(default=0)

    class Meta:
        ordering = ["order_index", "points_required"]

    def __str__(self):
        return f"{self.name_ar} ({self.points_required} pts)"


class TableCall(models.Model):
    """One call from a table (waiter, bill or hookah) and how staff handled it."""
    TYPE_CHOICES = [('call', 'Waiter'), ('bill', 'Bill'), ('hookah', 'Hookah')]
    table_number = models.CharField(max_length=10)
    call_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_by = models.ForeignKey(StaffUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_calls")
    accepted_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Table {self.table_number} {self.call_type} @ {self.created_at:%H:%M}"


class StaffShift(models.Model):
    """A staff member's working shift, started and ended from the staff app."""
    staff = models.ForeignKey(StaffUser, on_delete=models.CASCADE, related_name="shifts")
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    start_ip = models.CharField(max_length=64, blank=True)
    end_ip = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.staff.employee_name} {self.started_at:%Y-%m-%d %H:%M}"


class WhatsAppMessage(models.Model):
    """Every WhatsApp message the cafe sends or receives, with Meta's delivery status."""
    direction = models.CharField(max_length=3, choices=[("OUT", "Sent"), ("IN", "Received")])
    phone = models.CharField(max_length=20, db_index=True)
    kind = models.CharField(max_length=20, help_text="otp, welcome, points, winner, reply, incoming")
    text = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default="pending", help_text="pending, sent, delivered, read, failed, received")
    error = models.CharField(max_length=500, blank=True, default="")
    wa_message_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.direction} {self.kind} {self.phone} ({self.status})"
