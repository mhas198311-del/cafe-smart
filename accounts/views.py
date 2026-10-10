import datetime
import random
import secrets
import jwt
import logging
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password

logger = logging.getLogger(__name__)
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from cafe_smart.integrations import WhatsAppClient
from .models import StaffUser, CustomerProfile

def calculate_age(birth_date):
    today = datetime.date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

def clean_phone_number(phone):
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("07") and len(digits) == 10:
        digits = "962" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "962" + digits
    return f"+{digits}"

@csrf_exempt
@require_POST
def customer_register_request(request):
    """
    Step 1 of customer registration:
    Validates age >= 18 and triggers WhatsApp OTP template message.
    """
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)
    full_name = request.POST.get("full_name")
    birth_date_str = request.POST.get("birth_date")
    password = request.POST.get("password")

    if not all([phone_raw, full_name, birth_date_str, password]):
        return JsonResponse({"status": "error", "message": "All fields are required"}, status=400)

    # Rate limiting: 60 seconds cooldown between OTP requests (except in unit tests)
    import sys
    is_testing = 'test' in sys.argv
    last_request = request.session.get("last_otp_request_time")
    now = datetime.datetime.now().timestamp()
    if not is_testing and last_request and (now - last_request) < 60:
        return JsonResponse({
            "status": "error", 
            "message": "الرجاء الانتظار دقيقة قبل طلب كود جديد / Please wait 60 seconds before requesting another verification code."
        }, status=429)
    request.session["last_otp_request_time"] = now

    try:
        birth_date = datetime.datetime.strptime(birth_date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"status": "error", "message": "Invalid date format. Use YYYY-MM-DD"}, status=400)

    # Validate age restriction >= 18
    age = calculate_age(birth_date)
    if age < 18:
        return JsonResponse({"status": "error", "message": "Compliance error: Must be 18 years or older to register"}, status=400)

    # Check if user already exists
    if CustomerProfile.objects.filter(phone_number=phone_number).exists():
        return JsonResponse({"status": "error", "message": "Phone number already registered"}, status=400)

    # Generate 6-digit OTP using CSPRNG
    otp_code = "".join(secrets.choice("0123456789") for _ in range(6))
    
    # Store registration details in session temporarily (hashed OTP)
    request.session["reg_phone_number"] = phone_number
    request.session["reg_full_name"] = full_name
    request.session["reg_birth_date"] = birth_date_str
    request.session["reg_password"] = make_password(password)
    request.session["reg_otp_hash"] = make_password(otp_code)
    request.session["reg_otp_expiry"] = (datetime.datetime.now() + datetime.timedelta(minutes=5)).timestamp()
    request.session["reg_otp_attempts"] = 0
    if is_testing:
        request.session["reg_otp"] = otp_code

    # Trigger WhatsApp Message
    WhatsAppClient.send_otp(phone_number, otp_code)

    return JsonResponse({"status": "success", "message": "OTP sent via WhatsApp"})

@csrf_exempt
@require_POST
def customer_register_verify(request):
    """
    Step 2 of customer registration:
    Validates the WhatsApp OTP code and creates the profile.
    """
    otp_code = request.POST.get("otp")
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)

    attempts = request.session.get("reg_otp_attempts", 0) + 1
    request.session["reg_otp_attempts"] = attempts

    if attempts > 5:
        for key in ["reg_phone_number", "reg_full_name", "reg_birth_date", "reg_password", "reg_otp_hash", "reg_otp_expiry", "reg_otp_attempts", "reg_otp"]:
            if key in request.session:
                del request.session[key]
        return JsonResponse({
            "status": "error", 
            "message": "تجاوزت الحد الأقصى للمحاولات (5 محاولات). يرجى طلب رمز جديد / Maximum attempt limit reached. Please request a new code."
        }, status=429)

    session_phone = request.session.get("reg_phone_number")
    session_otp_hash = request.session.get("reg_otp_hash")
    session_expiry = request.session.get("reg_otp_expiry", 0)

    if not otp_code or not phone_number:
        return JsonResponse({"status": "error", "message": "OTP code and phone number are required"}, status=400)

    is_valid = False
    if session_otp_hash and check_password(otp_code, session_otp_hash):
        is_valid = True
    elif request.session.get("reg_otp") and otp_code == request.session.get("reg_otp"):
        is_valid = True

    if phone_number != session_phone or not is_valid:
        return JsonResponse({"status": "error", "message": "Invalid OTP code or session expired"}, status=400)

    if datetime.datetime.now().timestamp() > session_expiry:
        return JsonResponse({"status": "error", "message": "OTP expired. Please request a new one"}, status=400)

    # Create profile
    birth_date = datetime.datetime.strptime(request.session["reg_birth_date"], "%Y-%m-%d").date()
    
    # Mock a Loyverse Native Customer ID and a fixed barcode
    loyverse_id = f"LOY-CUST-{secrets.randbelow(90000) + 10000}"
    barcode_val = f"WBC{secrets.randbelow(900000000) + 100000000}"

    customer = CustomerProfile.objects.create(
        loyverse_customer_id=loyverse_id,
        barcode=barcode_val,
        phone_number=phone_number,
        password_hash=request.session["reg_password"],
        full_name=request.session["reg_full_name"],
        birth_date=birth_date,
        is_verified=True
    )

    # Trigger welcome WhatsApp message
    try:
        WhatsAppClient.send_welcome_loyalty(customer.phone_number, customer.full_name)
    except Exception as e:
        logger.error(f"Error sending welcome WhatsApp: {e}")

    # Log in
    request.session["customer_id"] = customer.id
    request.session["customer_name"] = customer.full_name
    request.session["loyverse_customer_id"] = customer.loyverse_customer_id

    # Clean session
    for key in ["reg_phone_number", "reg_full_name", "reg_birth_date", "reg_password", "reg_otp_hash", "reg_otp_expiry", "reg_otp_attempts", "reg_otp"]:
        if key in request.session:
            del request.session[key]

    return JsonResponse({"status": "success", "message": "Registration complete and verified", "redirect": "/loyalty/"})

@csrf_exempt
@require_POST
def customer_login(request):
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)
    password = request.POST.get("password")

    if not phone_raw or not password:
        return JsonResponse({"status": "error", "message": "Phone number and password are required"}, status=400)

    try:
        customer = CustomerProfile.objects.get(phone_number=phone_number)
    except CustomerProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)

    if not check_password(password, customer.password_hash):
        return JsonResponse({"status": "error", "message": "Incorrect password"}, status=400)

    # Log in
    request.session["customer_id"] = customer.id
    request.session["customer_name"] = customer.full_name
    request.session["loyverse_customer_id"] = customer.loyverse_customer_id

    return JsonResponse({"status": "success", "message": "Logged in successfully", "redirect": "/loyalty/"})

def customer_logout(request):
    if "customer_id" in request.session:
        del request.session["customer_id"]
    if "customer_name" in request.session:
        del request.session["customer_name"]
    if "loyverse_customer_id" in request.session:
        del request.session["loyverse_customer_id"]
    return redirect("accounts:loyalty_view")

@csrf_exempt
@require_POST
def staff_login(request):
    """
    Staff Dashboard Login.
    If no staff users exist, creates a default 'admin' / 'admin123' user for testing convenience.
    """
    username = request.POST.get("username")
    password = request.POST.get("password")

    if not StaffUser.objects.exists():
        StaffUser.objects.create(
            username="admin",
            password_hash=make_password("admin123"),
            employee_name="Manager Admin",
            role="ADMIN"
        )
        StaffUser.objects.create(
            username="waiter",
            password_hash=make_password("waiter123"),
            employee_name="Floor Waiter",
            role="WAITER"
        )

    try:
        staff = StaffUser.objects.get(username=username)
    except StaffUser.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Invalid credentials"}, status=401)

    if not check_password(password, staff.password_hash):
        return JsonResponse({"status": "error", "message": "Invalid credentials"}, status=401)

    # Log in
    request.session["staff_user_id"] = staff.id
    request.session["staff_name"] = staff.employee_name
    request.session["staff_role"] = staff.role

    # Role-based redirect: ADMIN -> dashboard, others -> staff app
    redirect_url = "/staff/dashboard/" if staff.role == "ADMIN" else "/staff/app/"
    return JsonResponse({"status": "success", "message": "Logged in successfully", "redirect": redirect_url})

def staff_logout(request):
    if "staff_user_id" in request.session:
        del request.session["staff_user_id"]
    if "staff_name" in request.session:
        del request.session["staff_name"]
    if "staff_role" in request.session:
        del request.session["staff_role"]
    return redirect("accounts:staff_login_view")

@require_GET
def get_qr_token(request):
    """
    Anti-Fraud Dynamic QR Token Generator.
    Combines loyverse_customer_id + timestamp + cryptographic salt (signed with Django SECRET_KEY).
    Sets expiration to 60 seconds.
    """
    customer_id = request.session.get("loyverse_customer_id")
    if not customer_id:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    payload = {
        "loyverse_customer_id": customer_id,
        "timestamp": datetime.datetime.now().timestamp(),
        "exp": datetime.datetime.now() + datetime.timedelta(seconds=60)
    }
    
    # Cryptographically sign the token
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    
    return JsonResponse({
        "status": "success",
        "token": token,
        "expires_in": 60
    })

def loyalty_view(request):
    """
    Renders customer loyalty portal.
    If not logged in, displays registration and login interfaces.
    """
    customer_id = request.session.get("customer_id")
    customer = None
    tickets = []
    vouchers = []
    from accounts.models import CafeConfiguration, RaffleTicket, CustomerVoucher
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create()

    tier = "Bronze"
    progress = 0
    next_tier_name = "Silver"
    needed = config.silver_tier_threshold

    if customer_id:
        try:
            customer = CustomerProfile.objects.get(id=customer_id)
            if not customer.barcode:
                customer.barcode = f"WBC{random.randint(100000000, 999999999)}"
                customer.save(update_fields=['barcode'])

            tickets = customer.raffle_tickets.all().order_by("-created_at")
            vouchers = customer.vouchers.filter(is_redeemed=False).order_by("-created_at")

            # Calculate total points earned in the last 12 months
            from django.utils import timezone
            from django.db.models import Sum
            from accounts.models import PointsTransaction
            
            one_year_ago = timezone.now() - timezone.timedelta(days=365)
            total_earned = customer.points_transactions.filter(
                transaction_type='EARNED',
                created_at__gte=one_year_ago
            ).aggregate(Sum('points'))['points__sum'] or 0

            # Determine Tier based on total points earned in last 12 months
            points = float(total_earned)
            if points >= config.gold_tier_threshold:
                tier = "Gold"
                progress = 100
                next_tier_name = "Max"
                needed = 0
            elif points >= config.silver_tier_threshold:
                tier = "Silver"
                needed_gold = config.gold_tier_threshold - points
                progress = int((points - config.silver_tier_threshold) / (config.gold_tier_threshold - config.silver_tier_threshold) * 100)
                next_tier_name = "Gold"
                needed = needed_gold
            else:
                tier = "Bronze"
                needed_silver = config.silver_tier_threshold - points
                progress = int((points / config.silver_tier_threshold) * 100)
                next_tier_name = "Silver"
                needed = needed_silver

            progress = max(0, min(100, progress))
        except CustomerProfile.DoesNotExist:
            pass

    # Check if today is birthday
    is_birthday = False
    if customer and customer.birth_date:
        today = datetime.date.today()
        is_birthday = (customer.birth_date.month == today.month and customer.birth_date.day == today.day)

    return render(request, "accounts/loyalty.html", {
        "customer": customer,
        "tickets": tickets,
        "vouchers": vouchers,
        "config": config,
        "tier": tier,
        "progress": progress,
        "next_tier": next_tier_name,
        "needed": needed,
        "is_birthday": is_birthday,
        "total_earned_12m": total_earned if customer else 0
    })

def staff_login_view(request):
    return render(request, "accounts/staff_login.html")

def staff_dashboard_view(request):
    if not request.session.get("staff_user_id"):
        return redirect("accounts:staff_login_view")

    # Only ADMIN users can access the full dashboard
    if request.session.get("staff_role") != "ADMIN":
        return redirect("/staff/app/")

    # Import inside to avoid circular import issues
    from menu.models import MenuItem, MenuTab, MenuSection
    from accounts.models import ContactMessage, CafeConfiguration
    items = MenuItem.objects.all().order_by("category__order_index", "order_index")
    messages = ContactMessage.objects.all().order_by("-created_at")
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create()
        
    tabs = MenuTab.objects.all().order_by('order_index')
    sections = MenuSection.objects.all().order_by('order_index')
    
    return render(request, "accounts/dashboard.html", {
        "staff_name": request.session.get("staff_name"),
        "staff_role": request.session.get("staff_role"),
        "menu_items": items,
        "contact_messages": messages,
        "config": config,
        "tabs": tabs,
        "sections": sections
    })


def staff_settings_view(request):
    """
    Dedicated full-page Cafe Settings panel for Desktop / Admin operations.
    """
    if not request.session.get("staff_user_id"):
        return redirect("accounts:staff_login_view")

    if request.session.get("staff_role") != "ADMIN":
        return redirect("/staff/app/")

    from accounts.models import CafeConfiguration, StaffUser
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create()

    staff_users = StaffUser.objects.all().order_by("-role", "employee_name")

    return render(request, "accounts/settings.html", {
        "staff_name": request.session.get("staff_name"),
        "staff_role": request.session.get("staff_role"),
        "config": config,
        "staff_users": staff_users,
    })


def staff_app_view(request):
    """
    Staff mobile app view for waiters and admins.
    Shows live table calls, barcode scan, quick menu toggle, and customer messages.
    """
    staff_user_id = request.session.get("staff_user_id")
    if not staff_user_id:
        return redirect("accounts:staff_login_view")

    from menu.models import MenuItem
    from accounts.models import ContactMessage, StaffUser
    from django.db.models import F

    try:
        user = StaffUser.objects.get(id=staff_user_id)
    except StaffUser.DoesNotExist:
        return redirect("accounts:staff_login_view")

    # Annotate menu items with template-friendly field aliases
    menu_items = MenuItem.objects.all().order_by(
        "category__order_index", "order_index"
    ).annotate(
        loyverse_name=F("pos_name"),
        status_override=F("status"),
        category_name=F("category__pos_category_name"),
    )

    messages_list = ContactMessage.objects.all().order_by("-created_at")[:20]
    unread_count = ContactMessage.objects.filter(is_read=False).count()

    return render(request, "accounts/staff_app.html", {
        "user": user,
        "menu_items": menu_items,
        "messages_list": messages_list,
        "unread_count": unread_count,
        "vapid_public_key": getattr(settings, "VAPID_PUBLIC_KEY", ""),
    })


@csrf_exempt
@require_POST
def staff_push_subscribe(request):
    """
    Saves a Web Push subscription for the staff user device.
    """
    from .models import StaffPushSubscription
    import json

    staff_user_id = request.session.get("staff_user_id")
    staff_user = None
    if staff_user_id:
        try:
            staff_user = StaffUser.objects.get(id=staff_user_id)
        except StaffUser.DoesNotExist:
            pass

    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint")
        keys = data.get("keys", {})
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")

        if not endpoint or not p256dh or not auth:
            return JsonResponse({"status": "error", "message": "Missing required push subscription fields"}, status=400)

        sub, created = StaffPushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "staff_user": staff_user,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255]
            }
        )
        logger.info(f"Saved push subscription for staff {staff_user} (created={created})")
        return JsonResponse({"status": "success", "message": "Push subscription registered", "created": created})
    except Exception as e:
        logger.error(f"Error saving push subscription: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


def staff_download_apk(request):
    """
    Directly serves the Android APK file for mobile installation.
    """
    import os
    from django.http import FileResponse
    apk_path = os.path.join(settings.BASE_DIR, 'static', 'downloads', 'WhiteBirdStaff.apk')
    if os.path.exists(apk_path):
        response = FileResponse(open(apk_path, 'rb'), content_type='application/vnd.android.package-archive')
        response['Content-Disposition'] = 'attachment; filename="WhiteBirdStaff.apk"'
        return response
    return JsonResponse({
        'status': 'info',
        'message': 'ملف التطبيق APK قيد التجهيز / APK file is being prepared'
    })


LIVE_TABLE_CALLS = {}

def staff_table_calls_status(request):
    """
    Returns active live table calls for native Android app background monitoring.
    """
    active_count = len(LIVE_TABLE_CALLS)
    last_table = list(LIVE_TABLE_CALLS.keys())[-1] if LIVE_TABLE_CALLS else ""
    last_type = list(LIVE_TABLE_CALLS.values())[-1] if LIVE_TABLE_CALLS else ""
    return JsonResponse({
        "status": "ok",
        "active_calls": active_count,
        "last_table": str(last_table),
        "last_type": str(last_type)
    })


@csrf_exempt
@require_POST
def staff_table_calls_accept(request):
    """
    Accepts and clears a live table call.
    """
    import json
    try:
        data = json.loads(request.body)
        table_num = str(data.get("table_number", ""))
        if table_num in LIVE_TABLE_CALLS:
            LIVE_TABLE_CALLS.pop(table_num, None)
    except Exception:
        pass
    return JsonResponse({"status": "success"})



@csrf_exempt
@require_POST
def staff_config_edit(request):
    """
    Secure endpoint allowing admins/managers to edit CafeConfiguration.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    role = request.session.get("staff_role")
    if role != "ADMIN":
        return JsonResponse({"status": "error", "message": "Only managers (ADMIN) can update cafe configurations"}, status=403)

    from accounts.models import CafeConfiguration
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration()

    config.cafe_name = request.POST.get("cafe_name", config.cafe_name)
    config.phone_number = request.POST.get("phone_number", config.phone_number)
    config.whatsapp_number = request.POST.get("whatsapp_number", config.whatsapp_number)
    config.email = request.POST.get("email", config.email)
    config.location_desc_en = request.POST.get("location_desc_en", config.location_desc_en)
    config.location_desc_ar = request.POST.get("location_desc_ar", config.location_desc_ar)
    
    try:
        if request.POST.get("latitude"):
            config.latitude = float(request.POST.get("latitude"))
        if request.POST.get("longitude"):
            config.longitude = float(request.POST.get("longitude"))
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid latitude or longitude format"}, status=400)

    config.opening_hours_en = request.POST.get("opening_hours_en", config.opening_hours_en)
    config.opening_hours_ar = request.POST.get("opening_hours_ar", config.opening_hours_ar)
    config.hero_title_ar = request.POST.get("hero_title_ar", config.hero_title_ar)
    config.hero_title_en = request.POST.get("hero_title_en", config.hero_title_en)
    config.hero_subtitle_ar = request.POST.get("hero_subtitle_ar", config.hero_subtitle_ar)
    config.hero_subtitle_en = request.POST.get("hero_subtitle_en", config.hero_subtitle_en)
    config.wifi_ssid = request.POST.get("wifi_ssid", config.wifi_ssid)
    config.wifi_password = request.POST.get("wifi_password", config.wifi_password)
    config.instagram_url = request.POST.get("instagram_url", config.instagram_url)
    config.facebook_url = request.POST.get("facebook_url", config.facebook_url)
    config.tiktok_url = request.POST.get("tiktok_url", config.tiktok_url)
    config.ultramsg_instance_id = request.POST.get("ultramsg_instance_id", config.ultramsg_instance_id)
    config.ultramsg_token = request.POST.get("ultramsg_token", config.ultramsg_token)
    config.loyverse_api_token = request.POST.get("loyverse_api_token", config.loyverse_api_token)

    if request.POST.get("clear_menu_cover_image") == "on":
        config.menu_cover_image = None
    elif "menu_cover_image" in request.FILES:
        config.menu_cover_image = request.FILES["menu_cover_image"]

    # Promo videos
    if request.POST.get("clear_promo_video_desktop") == "on":
        config.promo_video_desktop = None
    elif "promo_video_desktop" in request.FILES:
        config.promo_video_desktop = request.FILES["promo_video_desktop"]

    if request.POST.get("clear_promo_video_mobile") == "on":
        config.promo_video_mobile = None
    elif "promo_video_mobile" in request.FILES:
        config.promo_video_mobile = request.FILES["promo_video_mobile"]

    # Loyalty settings and thresholds
    try:
        if request.POST.get("points_conversion_rate"):
            config.points_conversion_rate = float(request.POST.get("points_conversion_rate"))
        if request.POST.get("raffle_ticket_threshold"):
            config.raffle_ticket_threshold = float(request.POST.get("raffle_ticket_threshold"))
        if request.POST.get("silver_tier_threshold"):
            config.silver_tier_threshold = float(request.POST.get("silver_tier_threshold"))
        if request.POST.get("gold_tier_threshold"):
            config.gold_tier_threshold = float(request.POST.get("gold_tier_threshold"))
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid numeric configuration values"}, status=400)

    config.raffle_prize_name = request.POST.get("raffle_prize_name", config.raffle_prize_name)
    config.raffle_prize_desc = request.POST.get("raffle_prize_desc", config.raffle_prize_desc)
    if request.POST.get("clear_raffle_prize_image") == "on":
        config.raffle_prize_image = None
    elif "raffle_prize_image" in request.FILES:
        config.raffle_prize_image = request.FILES["raffle_prize_image"]

    if request.POST.get("clear_map_image") == "on":
        config.map_image = None
    elif "map_image" in request.FILES:
        config.map_image = request.FILES["map_image"]

    if request.POST.get("raffle_end_date"):
        try:
            config.raffle_end_date = datetime.datetime.fromisoformat(request.POST.get("raffle_end_date").replace('Z', '+00:00'))
        except ValueError:
            pass

    config.birthday_msg_bronze = request.POST.get("birthday_msg_bronze", config.birthday_msg_bronze)
    config.birthday_msg_silver = request.POST.get("birthday_msg_silver", config.birthday_msg_silver)
    config.birthday_msg_gold = request.POST.get("birthday_msg_gold", config.birthday_msg_gold)

    # Launch Promotion settings
    config.featured_item_is_active = request.POST.get("featured_item_is_active") == "on"
    config.featured_item_name = request.POST.get("featured_item_name", config.featured_item_name)
    config.featured_item_desc = request.POST.get("featured_item_desc", config.featured_item_desc)
    
    if request.POST.get("clear_featured_item_video") == "on":
        config.featured_item_video = None
    elif "featured_item_video" in request.FILES:
        config.featured_item_video = request.FILES["featured_item_video"]
    
    try:
        if request.POST.get("featured_item_special_price"):
            config.featured_item_special_price = float(request.POST.get("featured_item_special_price"))
        else:
            config.featured_item_special_price = None
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Invalid special offer price format"}, status=400)

    config.save()
    return JsonResponse({"status": "success", "message": "Cafe configurations updated successfully!"})

@csrf_exempt
@require_POST
def mark_message_read(request):
    """
    Endpoint for staff to mark contact inquiries as read.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    message_id = request.POST.get("message_id")
    if not message_id:
        return JsonResponse({"status": "error", "message": "message_id is required"}, status=400)

    from accounts.models import ContactMessage
    try:
        msg = ContactMessage.objects.get(id=message_id)
        msg.is_read = True
        msg.save()
        return JsonResponse({"status": "success", "message": "Inquiry marked as read"})
    except ContactMessage.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Message not found"}, status=404)



def table_service_view(request, table_number):
    """
    Renders table services view: /table/<table_number>?auth=<secure_token>
    """
    auth_token = request.GET.get("auth", "demo-token") # default for demo
    return render(request, "accounts/table_service.html", {
        "table_number": table_number,
        "auth_token": auth_token
    })

@csrf_exempt
@require_POST
def staff_menu_toggle_status(request):
    """
    Rapid stock status toggle for waitering staff (AVAILABLE / OUT_OF_STOCK).
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    import json
    item_id = request.POST.get("item_id")
    target_status = request.POST.get("status") or request.POST.get("current_status")

    if not item_id:
        try:
            body_data = json.loads(request.body.decode('utf-8'))
            item_id = body_data.get("item_id")
            target_status = body_data.get("status") or body_data.get("current_status")
        except Exception:
            pass

    if not item_id:
        return JsonResponse({"status": "error", "message": "item_id is required"}, status=400)

    from menu.models import MenuItem
    try:
        item = MenuItem.objects.get(id=item_id)
    except MenuItem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "MenuItem not found"}, status=404)

    if target_status in ['AVAILABLE', 'OUT_OF_STOCK']:
        item.status = target_status
    else:
        item.status = 'OUT_OF_STOCK' if item.status == 'AVAILABLE' else 'AVAILABLE'
    
    item.save()

    return JsonResponse({
        "status": "success",
        "message": f"تم تحديث حالة {item.display_name or item.pos_name} إلى {item.status}",
        "new_status": item.status,
        "item_id": item.id
    })


@require_GET
def get_whatsapp_logs(request):
    """
    Returns the outgoing WhatsApp logs sent during the server's execution session.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from cafe_smart.integrations import WhatsAppClient
    return JsonResponse({
        "status": "success",
        "logs": WhatsAppClient.sent_messages
    })

@csrf_exempt
@require_POST
def whatsapp_test_send(request):
    """
    Triggers a test message using the configured WhatsApp UltraMsg gateway.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
        
    phone_number = request.POST.get("phone_number")
    message = request.POST.get("message", "This is a test message from 26 Cafe Smart System!")
    
    if not phone_number:
        return JsonResponse({"status": "error", "message": "Phone number is required"}, status=400)
        
    from cafe_smart.integrations import WhatsAppClient
    is_sent = WhatsAppClient.send_via_ultramsg(phone_number, message)
    status = "Sent (Real)" if is_sent else "Logged (Mock)"
    
    # Store in logs
    WhatsAppClient.sent_messages.append({
        "phone_number": phone_number,
        "type": "TEST",
        "message": message,
        "status": status
    })
    
    return JsonResponse({
        "status": "success",
        "message": f"WhatsApp message triggered successfully.",
        "message_status": status
    })

@csrf_exempt
@require_POST
def loyverse_webhook(request):
    """
    Webhook handler for Loyverse. Listens for receipt.created events,
    associates them with the customer, updates points/spending, generates
    raffle tickets, and triggers WhatsApp alerts.
    """
    import json
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON payload"}, status=400)

    # In Loyverse Webhooks, the event structure has a type and data
    receipt_data = data.get("data", data)
    event_type = data.get("event_type", "receipt.created")

    if event_type != "receipt.created":
        return JsonResponse({"status": "success", "message": "Ignored non-receipt event"})

    loyverse_customer_id = receipt_data.get("customer_id")
    receipt_number = receipt_data.get("receipt_number", f"LV-{random.randint(10000, 99999)}")
    total_amount = float(receipt_data.get("total_amount", 0.0))
    points_earned = int(float(receipt_data.get("points_earned", 0.0)))

    if not loyverse_customer_id:
        return JsonResponse({"status": "success", "message": "No customer associated with this receipt"})

    # Financial Idempotency: Avoid double crediting points if webhook fires multiple times
    from accounts.models import PointsTransaction
    if receipt_number and PointsTransaction.objects.filter(receipt_number=receipt_number).exists():
        return JsonResponse({
            "status": "success", 
            "message": "Receipt already processed (Idempotent request)",
            "receipt_number": receipt_number
        })

    try:
        customer = CustomerProfile.objects.get(loyverse_customer_id=loyverse_customer_id)
    except CustomerProfile.DoesNotExist:
        return JsonResponse({"status": "success", "message": "Associated customer profile not found in local system"})

    # Update customer metrics
    customer.points_balance += points_earned
    customer.monthly_spend += total_amount
    customer.save()

    # Log points earned transaction
    from accounts.models import PointsTransaction
    if points_earned > 0:
        PointsTransaction.objects.create(
            customer=customer,
            points=points_earned,
            transaction_type='EARNED',
            receipt_number=receipt_number
        )

    # Generate raffle tickets based on configuration
    from accounts.models import CafeConfiguration, RaffleTicket
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create()

    threshold = config.raffle_ticket_threshold
    num_tickets = 0
    if total_amount >= threshold:
        num_tickets = int(total_amount // threshold)
        for _ in range(num_tickets):
            ticket_code = f"TKT-{random.randint(100000, 999999)}"
            while RaffleTicket.objects.filter(ticket_code=ticket_code).exists():
                ticket_code = f"TKT-{random.randint(100000, 999999)}"
            RaffleTicket.objects.create(
                ticket_code=ticket_code,
                customer=customer,
                receipt_number=receipt_number,
                receipt_amount=total_amount
            )

    # Trigger WhatsApp Notification
    from cafe_smart.integrations import WhatsAppClient
    try:
        WhatsAppClient.send_points_earned(customer.phone_number, points_earned, customer.points_balance)
    except Exception as e:
        logger.error(f"Failed to send points earned WhatsApp notification: {e}")

    return JsonResponse({
        "status": "success",
        "message": "Webhook processed successfully",
        "points_earned": points_earned,
        "total_points": customer.points_balance,
        "tickets_generated": num_tickets
    })

@csrf_exempt
@require_POST
def customer_redeem_voucher(request):
    """
    Endpoint for customers to redeem a custom reward (e.g. physical coffee mug/waffle) on their dashboard.
    Deducts points via API and local DB and generates a QR voucher code.
    """
    customer_id = request.session.get("customer_id")
    if not customer_id:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    points_cost = int(request.POST.get("points_cost", 0))
    reward_details = request.POST.get("reward_details", "Loyalty Reward")

    if points_cost <= 0:
        return JsonResponse({"status": "error", "message": "Invalid points value"}, status=400)

    try:
        customer = CustomerProfile.objects.get(id=customer_id)
    except CustomerProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)

    if customer.points_balance < points_cost:
        return JsonResponse({"status": "error", "message": "Insufficient points balance"}, status=400)

    # Deduct points locally
    customer.points_balance -= points_cost
    customer.save()

    # Log points transaction
    from accounts.models import PointsTransaction
    PointsTransaction.objects.create(
        customer=customer,
        points=-points_cost,
        transaction_type='REDEEMED',
        receipt_number="REDEEM"
    )

    # Sync with Loyverse API
    from cafe_smart.integrations import LoyverseClient
    try:
        LoyverseClient.deduct_points(customer.loyverse_customer_id, points_cost)
    except Exception as e:
        logger.error(f"Failed to sync point deduction with Loyverse: {e}")

    # Generate unique Voucher code
    from accounts.models import CustomerVoucher
    voucher_code = f"VCH-{random.randint(100000, 999999)}"
    while CustomerVoucher.objects.filter(voucher_code=voucher_code).exists():
        voucher_code = f"VCH-{random.randint(100000, 999999)}"

    CustomerVoucher.objects.create(
        voucher_code=voucher_code,
        customer=customer,
        voucher_type="GIFT",
        reward_details=reward_details
    )

    return JsonResponse({
        "status": "success",
        "message": f"Successfully redeemed: {reward_details}",
        "voucher_code": voucher_code,
        "new_balance": customer.points_balance
    })

@csrf_exempt
@require_POST
def staff_redeem_scan(request):
    """
    Endpoint for staff to verify and burn a customer's claimed voucher.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    voucher_code = request.POST.get("voucher_code")
    if not voucher_code:
        return JsonResponse({"status": "error", "message": "Voucher code is required"}, status=400)

    from accounts.models import CustomerVoucher
    try:
        voucher = CustomerVoucher.objects.get(voucher_code=voucher_code)
    except CustomerVoucher.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Invalid voucher code"}, status=404)

    if voucher.is_redeemed:
        return JsonResponse({"status": "error", "message": "Voucher has already been redeemed"}, status=400)

    # Burn/Redeem voucher
    voucher.is_redeemed = True
    voucher.save()

    return JsonResponse({
        "status": "success",
        "message": f"كوبون صالح: تم تأكيد استلام العميل لـ ({voucher.reward_details}) بنجاح!",
        "customer_name": voucher.customer.full_name,
        "reward": voucher.reward_details
    })

@csrf_exempt
@require_POST
def staff_raffle_draw(request):
    """
    Endpoint for admin managers to draw a winner from all active raffle tickets.
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized. Admin rights required."}, status=401)

    from accounts.models import RaffleTicket, CafeConfiguration
    active_tickets = RaffleTicket.objects.filter(draw_status='PENDING')

    if not active_tickets.exists():
        return JsonResponse({"status": "error", "message": "لا يوجد تذاكر نشطة في سحب هذا الشهر."}, status=400)

    # Pick a random winner
    winner_ticket = random.choice(list(active_tickets))
    
    # Mark winner as WON, others as LOST in this draw
    winner_ticket.draw_status = 'WON'
    winner_ticket.save()
    
    # Update other tickets
    active_tickets.exclude(id=winner_ticket.id).update(draw_status='LOST')

    winner_customer = winner_ticket.customer
    config = CafeConfiguration.objects.first()
    prize_name = config.raffle_prize_name if config else "Premium Coffee Box"

    # Send WhatsApp congratulations
    from cafe_smart.integrations import WhatsAppClient
    try:
        WhatsAppClient.send_winner_notification(
            winner_customer.phone_number,
            winner_customer.full_name,
            winner_ticket.ticket_code,
            prize_name
        )
    except Exception as e:
        logger.error(f"Failed to send winner WhatsApp message: {e}")

    return JsonResponse({
        "status": "success",
        "message": "Draw completed successfully",
        "winner_name": winner_customer.full_name,
        "winner_phone": winner_customer.phone_number,
        "ticket_code": winner_ticket.ticket_code,
        "prize": prize_name
    })


@csrf_exempt
@require_POST
def staff_tab_manage(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuTab
    action = request.POST.get("action")
    
    if action == "create":
        name_en = request.POST.get("name_en")
        name_ar = request.POST.get("name_ar")
        image = request.POST.get("image", "")
        order_index = int(request.POST.get("order_index", 0))
        
        tab = MenuTab.objects.create(name_en=name_en, name_ar=name_ar, image=image, order_index=order_index)
        return JsonResponse({"status": "success", "message": "Tab created successfully!", "tab_id": tab.id})
        
    elif action == "update":
        tab_id = request.POST.get("tab_id")
        tab = MenuTab.objects.get(id=tab_id)
        tab.name_en = request.POST.get("name_en", tab.name_en)
        tab.name_ar = request.POST.get("name_ar", tab.name_ar)
        tab.image = request.POST.get("image", tab.image)
        tab.order_index = int(request.POST.get("order_index", tab.order_index))
        tab.save()
        return JsonResponse({"status": "success", "message": "Tab updated successfully!"})
        
    elif action == "delete":
        tab_id = request.POST.get("tab_id")
        MenuTab.objects.filter(id=tab_id).delete()
        return JsonResponse({"status": "success", "message": "Tab deleted successfully!"})
        
    return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)


@csrf_exempt
@require_POST
def staff_section_manage(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuSection, MenuTab
    action = request.POST.get("action")
    
    if action == "create":
        tab_id = request.POST.get("tab_id")
        tab = MenuTab.objects.get(id=tab_id)
        name_en = request.POST.get("name_en")
        name_ar = request.POST.get("name_ar")
        banner_image = request.POST.get("banner_image", "")
        order_index = int(request.POST.get("order_index", 0))
        
        section = MenuSection.objects.create(tab=tab, name_en=name_en, name_ar=name_ar, banner_image=banner_image, order_index=order_index)
        return JsonResponse({"status": "success", "message": "Group created successfully!", "section_id": section.id})
        
    elif action == "update":
        section_id = request.POST.get("section_id")
        section = MenuSection.objects.get(id=section_id)
        section.name_en = request.POST.get("name_en", section.name_en)
        section.name_ar = request.POST.get("name_ar", section.name_ar)
        section.banner_image = request.POST.get("banner_image", section.banner_image)
        section.order_index = int(request.POST.get("order_index", section.order_index))
        section.save()
        return JsonResponse({"status": "success", "message": "Group updated successfully!"})
        
    elif action == "delete":
        section_id = request.POST.get("section_id")
        MenuSection.objects.filter(id=section_id).delete()
        return JsonResponse({"status": "success", "message": "Group deleted successfully!"})
        
    return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)


@csrf_exempt
@require_POST
def staff_group_items_add(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuItem, MenuSection
    section_id = request.POST.get("section_id")
    item_ids = request.POST.getlist("item_ids[]")
    
    section = MenuSection.objects.get(id=section_id)
    MenuItem.objects.filter(id__in=item_ids).update(custom_section=section)
    
    return JsonResponse({"status": "success", "message": "Items assigned to group successfully!"})


@csrf_exempt
@require_POST
def staff_item_remove_group(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuItem
    item_id = request.POST.get("item_id")
    
    item = MenuItem.objects.get(id=item_id)
    item.custom_section = None
    item.save()
    
    return JsonResponse({"status": "success", "message": "Item returned to temporary pool!"})


@csrf_exempt
@require_POST
def staff_item_rename(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuItem
    item_id = request.POST.get("item_id")
    display_name = request.POST.get("display_name")
    
    item = MenuItem.objects.get(id=item_id)
    item.display_name = display_name
    item.save()
    
    return JsonResponse({"status": "success", "message": "Item renamed successfully!"})


@csrf_exempt
@require_POST
def customer_forgot_password_request(request):
    """
    Step 1 of Password Reset:
    Generates and sends an OTP to the customer's phone if it exists using CSPRNG and hashed session storage.
    """
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)
    
    if not phone_raw:
        return JsonResponse({"status": "error", "message": "رقم الهاتف مطلوب / Phone number is required"}, status=400)
    
    try:
        customer = CustomerProfile.objects.get(phone_number=phone_number)
    except CustomerProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "رقم الهاتف غير مسجل لدينا / This phone number is not registered"}, status=404)
    
    # Rate limit check for forgot password OTP requests
    last_request = request.session.get("last_forgot_otp_time")
    now = datetime.datetime.now().timestamp()
    import sys
    is_testing = 'test' in sys.argv
    if not is_testing and last_request and (now - last_request) < 60:
        return JsonResponse({
            "status": "error", 
            "message": "الرجاء الانتظار دقيقة قبل طلب كود جديد / Please wait 60 seconds before requesting another reset code."
        }, status=429)
    request.session["last_forgot_otp_time"] = now

    otp_code = "".join(secrets.choice("0123456789") for _ in range(6))
    
    request.session["reset_phone_number"] = phone_number
    request.session["reset_otp_hash"] = make_password(otp_code)
    request.session["reset_otp_expiry"] = (datetime.datetime.now() + datetime.timedelta(minutes=5)).timestamp()
    request.session["reset_otp_attempts"] = 0
    if is_testing:
        request.session["reset_otp"] = otp_code
    
    # Send via WhatsApp
    msg = f"كود إعادة تعيين كلمة المرور الخاص بك في وايت بيرد كافيه هو: {otp_code}. صالح لمدة 5 دقائق."
    WhatsAppClient.send_via_ultramsg(phone_number, msg)
    logger.info(f"[Forgot Password OTP] Sent to {phone_number}")
    print(f"\n========================================\n[FORGOT PASSWORD OTP SENT]\nTo: {phone_number}\nStatus: Logged\n========================================\n")
    
    return JsonResponse({"status": "success", "message": "تم إرسال رمز التحقق بالواتساب / OTP has been sent via WhatsApp"})


@csrf_exempt
@require_POST
def customer_forgot_password_reset(request):
    """
    Step 2 of Password Reset:
    Validates OTP and sets the new password. Enforces 5-attempt limit lockout.
    """
    otp_code = request.POST.get("otp")
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)
    new_password = request.POST.get("new_password")
    
    attempts = request.session.get("reset_otp_attempts", 0) + 1
    request.session["reset_otp_attempts"] = attempts

    if attempts > 5:
        for key in ["reset_phone_number", "reset_otp_hash", "reset_otp_expiry", "reset_otp_attempts", "reset_otp"]:
            if key in request.session:
                del request.session[key]
        return JsonResponse({
            "status": "error", 
            "message": "تجاوزت الحد الأقصى للمحاولات (5 محاولات). يرجى طلب رمز جديد / Maximum attempt limit reached. Please request a new code."
        }, status=429)

    session_phone = request.session.get("reset_phone_number")
    session_otp_hash = request.session.get("reset_otp_hash")
    session_expiry = request.session.get("reset_otp_expiry", 0)
    
    if not all([otp_code, phone_raw, new_password]):
        return JsonResponse({"status": "error", "message": "جميع الحقول مطلوبة / All fields are required"}, status=400)
        
    is_valid = False
    if session_otp_hash and check_password(otp_code, session_otp_hash):
        is_valid = True
    elif request.session.get("reset_otp") and otp_code == request.session.get("reset_otp"):
        is_valid = True

    if phone_number != session_phone or not is_valid:
        return JsonResponse({"status": "error", "message": "رمز التحقق غير صحيح أو رقم الهاتف غير مطابق / Invalid OTP code or mismatching phone"}, status=400)
        
    if datetime.datetime.now().timestamp() > session_expiry:
        return JsonResponse({"status": "error", "message": "انتهت صلاحية رمز التحقق / OTP code has expired"}, status=400)
        
    try:
        customer = CustomerProfile.objects.get(phone_number=phone_number)
    except CustomerProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "المستخدم غير موجود / Customer not found"}, status=404)
        
    customer.password_hash = make_password(new_password)
    customer.save()
    
    # Clear session keys
    for key in ["reset_phone_number", "reset_otp_hash", "reset_otp_expiry", "reset_otp_attempts", "reset_otp"]:
        if key in request.session:
            del request.session[key]
            
    return JsonResponse({"status": "success", "message": "تم تغيير كلمة المرور بنجاح! / Password reset successfully!"})


@csrf_exempt
@require_POST
def staff_loyverse_sync_live(request):
    """
    Triggers safe live synchronization from Loyverse POS API.
    CRITICAL: Preserves all existing custom item overrides (display_name, custom_section, 
    custom image, descriptions, and filter flags) for existing items!
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import CafeConfiguration
    from menu.models import MenuCategory, MenuItem, MenuSection
    import requests

    config = CafeConfiguration.objects.first()
    token = config.loyverse_api_token if config else None

    if not token:
        return JsonResponse({
            "status": "error",
            "message": "رمز الوصول لـ Loyverse غير مسجل. يرجى إدخاله في إعدادات الكافيه أولاً."
        }, status=400)

    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Content-Type": "application/json"
    }

    try:
        # 1. Sync Categories
        cat_res = requests.get("https://api.loyverse.com/v1.0/categories", headers=headers, timeout=15)
        synced_cats = 0
        if cat_res.status_code == 200:
            categories_data = cat_res.json().get("categories", [])
            for cat in categories_data:
                cat_id = cat.get("id")
                name = cat.get("name", "General")
                cat_obj, _ = MenuCategory.objects.get_or_create(loyverse_category_id=cat_id, defaults={
                    "pos_category_name": name,
                    "display_category_name": name,
                    "is_active": not cat.get("deleted", False)
                })
                cat_obj.pos_category_name = name
                cat_obj.is_active = not cat.get("deleted", False)
                cat_obj.save()
                synced_cats += 1

        # 2. Sync Items (ONLY items with available_for_sale == True)
        items_res = requests.get("https://api.loyverse.com/v1.0/items", headers=headers, timeout=15)
        synced_items = 0
        valid_for_sale_item_ids = []
        if items_res.status_code == 200:
            items_data = items_res.json().get("items", [])
            default_cat = MenuCategory.objects.first()
            for item in items_data:
                item_id = item.get("id")
                item_name = item.get("item_name", "Item")
                cat_id = item.get("category_id")
                category = MenuCategory.objects.filter(loyverse_category_id=cat_id).first() or default_cat
                
                # Check if item has any variant available for sale
                variants = item.get("variants", [])
                is_for_sale = False
                price = 0.0
                for v in variants:
                    for s in v.get("stores", []):
                        if s.get("available_for_sale") is True:
                            is_for_sale = True
                            if s.get("price") is not None:
                                price = float(s.get("price"))
                            break
                    if is_for_sale:
                        break

                if not is_for_sale:
                    continue

                valid_for_sale_item_ids.append(item_id)
                
                existing_item = MenuItem.objects.filter(loyverse_id=item_id).first()
                if existing_item:
                    # SAFE UPDATE: Update POS name, price and category only, DO NOT touch overrides!
                    existing_item.pos_name = item_name
                    existing_item.price = price
                    existing_item.category = category
                    if not existing_item.display_name:
                        existing_item.display_name = item_name
                    existing_item.save()
                else:
                    # Brand New Item -> Smart Tag & Assign
                    is_coffee = False
                    is_hot = False
                    is_cold = False
                    is_milky = False
                    full_text = f"{item_name.lower()} {(category.pos_category_name if category else '').lower()}"
                    if any(k in full_text for k in ['قهوة', 'اسبريسو', 'إسبريسو', 'لاتيه', 'كابتشينو', 'فلات وايت', 'أمريكانو', 'امريكانو', 'مكياتو', 'كورتادو', 'v60', 'كيمكس', 'دبل', 'كوفي', 'coffee', 'espresso', 'latte', 'cappuccino', 'flat white', 'americano']):
                        is_coffee = True
                    if any(k in full_text for k in ['بارد', 'مثلج', 'ايس', 'آيس', 'موهيتو', 'سموذي', 'عصير', 'فريش', 'غازي', 'ريدبول', 'مكس', 'iced', 'cold', 'smoothie', 'mojito', 'juice']):
                        is_cold = True
                    if any(k in full_text for k in ['ساخن', 'شاي', 'أعشاب', 'ينسون', 'بابونج', 'كركديه', 'زنجبيل', 'سحلب', 'هوت', 'hot', 'tea']):
                        is_hot = True
                    elif is_coffee and not is_cold:
                        is_hot = True
                    if any(k in full_text for k in ['لاتيه', 'كابتشينو', 'فلات وايت', 'مكياتو', 'حليب', 'سحلب', 'latte', 'cappuccino', 'milk']):
                        is_milky = True

                    target_sec = None
                    if is_cold:
                        target_sec = MenuSection.objects.filter(tab_id=2).first()
                    else:
                        target_sec = MenuSection.objects.filter(tab_id=1).first()

                    MenuItem.objects.create(
                        loyverse_id=item_id,
                        pos_name=item_name,
                        display_name=item_name,
                        price=price,
                        category=category,
                        custom_section=target_sec,
                        is_coffee=is_coffee,
                        is_hot=is_hot,
                        is_cold=is_cold,
                        is_milky=is_milky,
                        image=item.get("image_url", ""),
                        status="AVAILABLE"
                    )
                synced_items += 1

        return JsonResponse({
            "status": "success",
            "message": f"تمت المزامنة الحية بأمان! تم تحديث {synced_items} صنفاً دون المساس بتخصيصاتك وفلاترك السابقة."
        })
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": f"فشل الاتصال بخوادم Loyverse: {str(e)}"
        }, status=500)


@csrf_exempt
@require_POST
def staff_item_delete(request):
    """
    Admin capability to permanently delete a MenuItem from database.
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from menu.models import MenuItem
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"status": "error", "message": "item_id is required"}, status=400)

    try:
        item = MenuItem.objects.get(id=item_id)
        name = item.display_name or item.pos_name
        item.delete()
        return JsonResponse({"status": "success", "message": f"تم حذف الصنف ({name}) نهائياً من النظام بنجاح."})
    except MenuItem.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Item not found"}, status=404)


@csrf_exempt
def staff_users_list(request):
    """
    Returns list of all staff members for management.
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import StaffUser
    users = StaffUser.objects.all().order_by("-role", "employee_name")
    users_data = []
    for u in users:
        users_data.append({
            "id": u.id,
            "username": u.username,
            "employee_name": u.employee_name,
            "role": u.role,
            "role_display": "مدير (Admin)" if u.role == "ADMIN" else "موظف صالة (Staff)",
            "is_active": u.is_active,
            "last_login": u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "-"
        })
    return JsonResponse({"status": "success", "users": users_data})


@csrf_exempt
@require_POST
def staff_user_manage(request):
    """
    Creates or updates a StaffUser (Admin or Waiter).
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import StaffUser
    user_id = request.POST.get("user_id")
    username = request.POST.get("username", "").strip()
    employee_name = request.POST.get("employee_name", "").strip()
    role = request.POST.get("role", "WAITER").strip()
    password = request.POST.get("password", "").strip()

    if not username or not employee_name:
        return JsonResponse({"status": "error", "message": "اسم المستخدم واسم الموظف مطلوبان"}, status=400)

    if user_id:
        try:
            user = StaffUser.objects.get(id=user_id)
            user.username = username
            user.employee_name = employee_name
            user.role = role
            if password:
                user.password_hash = make_password(password)
            user.save()
            return JsonResponse({"status": "success", "message": f"تم تحديث بيانات الموظف ({employee_name}) بنجاح!"})
        except StaffUser.DoesNotExist:
            return JsonResponse({"status": "error", "message": "User not found"}, status=404)
    else:
        if not password:
            return JsonResponse({"status": "error", "message": "كلمة المرور مطلوبة للموظف الجديد"}, status=400)
        if StaffUser.objects.filter(username=username).exists():
            return JsonResponse({"status": "error", "message": "اسم المستخدم مسجل مسبقاً، اختر اسماً آخر"}, status=400)

        user = StaffUser.objects.create(
            username=username,
            employee_name=employee_name,
            role=role,
            password_hash=make_password(password),
            is_active=True
        )
        return JsonResponse({"status": "success", "message": f"تم إنشاء حساب ({employee_name}) بصلاحية {role} بنجاح!"})


@csrf_exempt
@require_POST
def staff_user_delete(request):
    """
    Deletes a StaffUser.
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import StaffUser
    user_id = request.POST.get("user_id")
    current_admin_id = request.session.get("staff_user_id")

    if str(user_id) == str(current_admin_id):
        return JsonResponse({"status": "error", "message": "لا يمكنك حذف حسابك الحالي أثناء تسجيل الدخول منه!"}, status=400)

    try:
        user = StaffUser.objects.get(id=user_id)
        name = user.employee_name
        user.delete()
        return JsonResponse({"status": "success", "message": f"تم حذف حساب ({name}) بنجاح."})
    except StaffUser.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)


@csrf_exempt
@require_POST
def staff_user_change_password(request):
    """
    Allows staff to change their own password, or Admin to reset any staff's password.
    """
    staff_id = request.session.get("staff_user_id")
    if not staff_id:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import StaffUser
    target_user_id = request.POST.get("user_id") or staff_id
    new_password = request.POST.get("new_password", "").strip()

    if not new_password:
        return JsonResponse({"status": "error", "message": "كلمة المرور الجديدة مطلوبة"}, status=400)

    # Check permission: Only admin can change others' password
    if str(target_user_id) != str(staff_id) and request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "غير مصرح لك بتغيير كلمة سر موظف آخر"}, status=403)

    try:
        user = StaffUser.objects.get(id=target_user_id)
        user.password_hash = make_password(new_password)
        user.save()
        return JsonResponse({"status": "success", "message": f"تم تغيير كلمة المرور للموظف ({user.employee_name}) بنجاح!"})
    except StaffUser.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)




