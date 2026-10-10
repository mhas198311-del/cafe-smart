from django.db import models

class StaffUser(models.Model):
    ROLE_CHOICES = [('ADMIN', 'Owner/Manager'), ('WAITER', 'Floor Staff')]
    username = models.CharField(max_length=150, unique=True)
    password_hash = models.CharField(max_length=255, help_text="Argon2 or BCrypt hash")
    employee_name = models.CharField(max_length=255)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='WAITER')
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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name

class CafeConfiguration(models.Model):
    cafe_name = models.CharField(max_length=255, default="White Bird Cafe", help_text="The name of the cafe displayed on the website")
    phone_number = models.CharField(max_length=50, default="+962 79 123 4567")
    whatsapp_number = models.CharField(max_length=50, default="962791234567", help_text="WhatsApp format without + or spaces, e.g. 962791234567")
    email = models.EmailField(default="info@26cafe.com")
    location_desc_en = models.CharField(max_length=255, default="Wadi Musa, Petra Road - Near Petra Visitor Center")
    location_desc_ar = models.CharField(max_length=255, default="وادي موسى، شارع البتراء - بالقرب من مركز زوار البتراء")
    latitude = models.FloatField(default=30.3222)
    longitude = models.FloatField(default=35.4795)
    opening_hours_en = models.CharField(max_length=255, default="Daily: 7:30 AM - 11:30 PM")
    opening_hours_ar = models.CharField(max_length=255, default="يومياً: 7:30 صباحاً - 11:30 مساءً")
    instagram_url = models.URLField(default="https://instagram.com/26cafe", blank=True, null=True)
    facebook_url = models.URLField(default="https://facebook.com/26cafe", blank=True, null=True)
    tiktok_url = models.URLField(default="https://tiktok.com/@26cafe", blank=True, null=True)
    
    # UltraMsg WhatsApp Credentials
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
    birthday_msg_bronze = models.TextField(default="Happy Birthday! Enjoy 15% off today.")
    birthday_msg_silver = models.TextField(default="Happy Birthday! Enjoy a free drink today.")
    birthday_msg_gold = models.TextField(default="Happy Birthday! Enjoy a free drink + dessert today.")

    # New Item Launch Promotion (Cinematic video & Special Offer)
    featured_item_is_active = models.BooleanField(default=False, help_text="Toggle to display a special featured item launch card on the menu")
    featured_item_name = models.CharField(max_length=255, blank=True, null=True, default="Saffron Latte / لاتيه الزعفران")
    featured_item_desc = models.TextField(blank=True, null=True, default="جرب صنفنا الجديد المميز بنكهة الزعفران الفاخرة الممزوجة مع الإسبريسو العضوي.")
    featured_item_video = models.FileField(upload_to='videos/', blank=True, null=True, help_text="Promo video file for the new item")
    featured_item_special_price = models.DecimalField(max_digits=10, decimal_places=3, blank=True, null=True, help_text="Discounted price for trying the new item")

    def __str__(self):
        return "26 Cafe Configuration"

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

class RaffleTicket(models.Model):
    ticket_code = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name='raffle_tickets')
    receipt_number = models.CharField(max_length=100)
    receipt_amount = models.FloatField()
    draw_status = models.CharField(max_length=20, default='PENDING', help_text="PENDING, WON, or LOST")
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

