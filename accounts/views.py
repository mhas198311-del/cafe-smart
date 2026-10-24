import datetime
import os
import random
import secrets
import jwt
import logging
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password

logger = logging.getLogger(__name__)
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from cafe_smart.integrations import WhatsAppClient, LoyverseClient, LoyverseError
from .models import StaffUser, CustomerProfile
from .security import client_ip, hit_limit, is_limited, reset_limit, staff_is_admin, network_ip, on_cafe_network

MIN_CUSTOMER_PASSWORD_LENGTH = 6

def _request_data(request):
    """
    Returns the submitted fields whether the page sent a normal form (FormData)
    or a JSON body, so both kinds of fetch() calls from the templates work.
    """
    if request.content_type == "application/json":
        import json
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST

def get_customer_tier(customer, config):
    """Tier name from points earned in the last 12 months (same rule as the loyalty page)."""
    from django.utils import timezone
    from django.db.models import Sum
    one_year_ago = timezone.now() - timezone.timedelta(days=365)
    total_earned = customer.points_transactions.filter(
        transaction_type='EARNED',
        created_at__gte=one_year_ago
    ).aggregate(Sum('points'))['points__sum'] or 0
    if config and total_earned >= config.gold_tier_threshold:
        return "Gold"
    if config and total_earned >= config.silver_tier_threshold:
        return "Silver"
    return "Bronze"

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
        return JsonResponse({"status": "error", "message": "عبّي كل الخانات / All fields are required"}, status=400)

    if len(password) < MIN_CUSTOMER_PASSWORD_LENGTH:
        return JsonResponse({"status": "error", "message": "كلمة المرور يجب أن تكون 6 أحرف على الأقل / Password must be at least 6 characters"}, status=400)

    # Every OTP is a paid WhatsApp message: cap per phone and per visitor
    if hit_limit(f"otp:phone:{phone_number}", 3, 3600) or hit_limit(f"otp:ip:{client_ip(request)}", 10, 3600):
        return JsonResponse({
            "status": "error",
            "message": "تم طلب رموز كثيرة، حاول بعد ساعة / Too many codes requested, try again in an hour."
        }, status=429)

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
        return JsonResponse({"status": "error", "message": "تاريخ الميلاد غير صحيح / Invalid date of birth"}, status=400)

    # Validate age restriction >= 18
    age = calculate_age(birth_date)
    if age < 18:
        return JsonResponse({"status": "error", "message": "التسجيل لعمر 18 سنة وفوق / You must be 18 or older to register"}, status=400)

    # Check if user already exists
    if CustomerProfile.objects.filter(phone_number=phone_number).exists():
        return JsonResponse({"status": "error", "message": "الرقم مسجّل من قبل، سجّل دخول / This number is already registered, please log in"}, status=400)

    # Generate 6-digit OTP using CSPRNG
    otp_code = "".join(secrets.choice("0123456789") for _ in range(6))
    
    # Store registration details in session temporarily (hashed OTP)
    request.session["reg_phone_number"] = phone_number
    request.session["reg_full_name"] = full_name
    request.session["reg_birth_date"] = birth_date_str
    request.session["reg_password"] = make_password(password)
    request.session["reg_marketing_opt_in"] = str(request.POST.get("marketing_opt_in", "")).lower() in ("1", "true", "on")
    request.session["reg_otp_hash"] = make_password(otp_code)
    request.session["reg_otp_expiry"] = (datetime.datetime.now() + datetime.timedelta(minutes=5)).timestamp()
    request.session["reg_otp_attempts"] = 0
    if is_testing:
        request.session["reg_otp"] = otp_code

    # Trigger WhatsApp Message
    if not WhatsAppClient.send_otp(phone_number, otp_code) and WhatsAppClient.is_configured():
        return JsonResponse({"status": "error", "message": "تعذر إرسال رمز التحقق على الواتساب، تأكد من الرقم وحاول مرة ثانية / Could not send the WhatsApp code"}, status=502)

    return JsonResponse({"status": "success", "message": "انبعت كود التحقق على الواتساب / Code sent on WhatsApp"})

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
        return JsonResponse({"status": "error", "message": "اكتب كود التحقق / Enter the verification code"}, status=400)

    is_valid = False
    if session_otp_hash and check_password(otp_code, session_otp_hash):
        is_valid = True
    elif request.session.get("reg_otp") and otp_code == request.session.get("reg_otp"):
        is_valid = True

    if phone_number != session_phone or not is_valid:
        return JsonResponse({"status": "error", "message": "الكود غلط أو انتهت صلاحيته / Wrong or expired code"}, status=400)

    if datetime.datetime.now().timestamp() > session_expiry:
        return JsonResponse({"status": "error", "message": "انتهت صلاحية الكود، اطلب كود جديد / The code expired, request a new one"}, status=400)

    # Create profile
    birth_date = datetime.datetime.strptime(request.session["reg_birth_date"], "%Y-%m-%d").date()
    
    # Temporary id until the customer is created/linked in Loyverse below
    loyverse_id = f"PENDING-{secrets.token_hex(8)}"
    barcode_val = f"WBC{secrets.randbelow(900000000) + 100000000}"

    customer = CustomerProfile.objects.create(
        loyverse_customer_id=loyverse_id,
        barcode=barcode_val,
        phone_number=phone_number,
        password_hash=request.session["reg_password"],
        full_name=request.session["reg_full_name"],
        birth_date=birth_date,
        is_verified=True,
        marketing_opt_in=bool(request.session.get("reg_marketing_opt_in")),
    )

    # Create (or reuse by phone) the customer in Loyverse. If Loyverse is unreachable
    # the account still works and is linked on the next visit to the loyalty page.
    if LoyverseClient.is_configured():
        try:
            LoyverseClient.link_customer(customer)
        except LoyverseError as e:
            logger.error(f"Could not link new customer {customer.id} to Loyverse: {e}")

    # Trigger welcome WhatsApp message
    try:
        WhatsAppClient.send_welcome_loyalty(customer.phone_number, customer.full_name)
    except Exception as e:
        logger.error(f"Error sending welcome WhatsApp: {e}")

    # Log in (new session id so an old one cannot be reused)
    request.session.cycle_key()
    request.session["customer_id"] = customer.id
    request.session["customer_name"] = customer.full_name
    request.session["loyverse_customer_id"] = customer.loyverse_customer_id

    # Clean session
    for key in ["reg_phone_number", "reg_full_name", "reg_birth_date", "reg_password", "reg_otp_hash", "reg_otp_expiry", "reg_otp_attempts", "reg_otp"]:
        if key in request.session:
            del request.session[key]

    return JsonResponse({"status": "success", "message": "تم التسجيل بنجاح / Registration complete", "redirect": "/loyalty/"})

@csrf_exempt
@require_POST
def customer_login(request):
    phone_raw = request.POST.get("phone_number")
    phone_number = clean_phone_number(phone_raw)
    password = request.POST.get("password")

    if not phone_raw or not password:
        return JsonResponse({"status": "error", "message": "اكتب رقم الهاتف وكلمة المرور / Enter your phone number and password"}, status=400)

    limit_key = f"login:customer:{phone_number}"
    if is_limited(limit_key, 10) or hit_limit(f"login:customer-ip:{client_ip(request)}", 30, 900):
        return JsonResponse({"status": "error", "message": "محاولات كثيرة، حاول بعد 15 دقيقة / Too many attempts, try again in 15 minutes"}, status=429)

    customer = CustomerProfile.objects.filter(phone_number=phone_number).first()
    if not customer or not check_password(password, customer.password_hash):
        hit_limit(limit_key, 10, 900)
        return JsonResponse({"status": "error", "message": "رقم الهاتف أو كلمة المرور غير صحيحة / Incorrect phone number or password"}, status=400)
    reset_limit(limit_key)

    # Log in
    request.session.cycle_key()
    request.session["customer_id"] = customer.id
    request.session["customer_name"] = customer.full_name
    request.session["loyverse_customer_id"] = customer.loyverse_customer_id

    return JsonResponse({"status": "success", "message": "تم تسجيل الدخول / Logged in", "redirect": "/loyalty/"})

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
    Staff Dashboard Login. The first manager account is created by seed.py
    from the INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD environment variables.
    """
    username = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""

    limit_key = f"login:staff:{username.lower()}"
    if is_limited(limit_key, 5) or hit_limit(f"login:staff-ip:{client_ip(request)}", 20, 900):
        return JsonResponse({"status": "error", "message": "محاولات كثيرة، حاول بعد 15 دقيقة / Too many attempts, try again in 15 minutes"}, status=429)

    staff = StaffUser.objects.filter(username__iexact=username).first()
    if not staff or not staff.is_active or not check_password(password, staff.password_hash):
        hit_limit(limit_key, 5, 900)
        return JsonResponse({"status": "error", "message": "البيانات غير صحيحة / Wrong login details"}, status=401)
    reset_limit(limit_key)

    # Log in
    request.session.cycle_key()
    request.session["staff_user_id"] = staff.id
    request.session["staff_name"] = staff.employee_name
    request.session["staff_role"] = staff.role

    # Role-based redirect: ADMIN -> dashboard, others -> staff app
    redirect_url = "/staff/dashboard/" if staff.role == "ADMIN" else "/staff/app/"
    return JsonResponse({"status": "success", "message": "تم تسجيل الدخول / Logged in", "redirect": redirect_url})

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
    points_live = False

    if customer_id:
        try:
            customer = CustomerProfile.objects.get(id=customer_id)
            if not customer.barcode:
                customer.barcode = f"WBC{secrets.randbelow(900000000) + 100000000}"
                customer.save(update_fields=['barcode'])
            points_live = LoyverseClient.refresh_customer_points(customer)

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

    from accounts.models import LoyaltyReward
    rewards = []
    if customer:
        for reward in LoyaltyReward.objects.filter(is_active=True):
            remaining = max(0, reward.points_required - customer.points_balance)
            reward.is_available = remaining == 0
            reward.points_remaining = remaining
            reward.progress = 100 if not reward.points_required else min(100, int(customer.points_balance * 100 / reward.points_required))
            rewards.append(reward)

    from .draws import get_draw, month_start, next_month, month_entries, month_name_ar, PLACE_NAMES
    from django.utils import timezone
    this_month = month_start()
    draw = get_draw(this_month)
    draw_prizes = [{"place": PLACE_NAMES[p], "name": n, "image": i} for p, n, i in draw.prizes() if n]
    my_entries = month_entries(this_month).filter(customer=customer).count() if customer else 0
    draw_ends = timezone.make_aware(datetime.datetime.combine(next_month(this_month), datetime.time.min))

    return render(request, "accounts/loyalty.html", {
        "customer": customer,
        "draw": draw,
        "draw_prizes": draw_prizes,
        "draw_month_name": month_name_ar(this_month),
        "draw_ends_iso": draw_ends.isoformat(),
        "my_entries": my_entries,
        "rewards": rewards,
        "points_live": points_live,
        "loyverse_enabled": LoyverseClient.is_configured(),
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
    from django.db.models import F
    items = MenuItem.objects.select_related("custom_section").order_by(
        F("custom_section").asc(nulls_first=True), "category__order_index", "order_index"
    )
    messages = ContactMessage.objects.all().order_by("-created_at")
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create()
        
    tabs = MenuTab.objects.all().order_by('order_index')
    sections = MenuSection.objects.all().order_by('order_index')
    from accounts.models import RaffleTicket
    pending_tickets_count = RaffleTicket.objects.filter(draw_status='PENDING').count()
    stats, customers = _dashboard_stats_and_customers(config)

    return render(request, "accounts/dashboard.html", {
        "stats": stats,
        "customers": customers,
        "staff_name": request.session.get("staff_name"),
        "staff_role": request.session.get("staff_role"),
        "menu_items": items,
        "pending_items_count": sum(1 for i in items if i.custom_section_id is None),
        "contact_messages": messages,
        "config": config,
        "tabs": tabs,
        "sections": sections,
        "pending_tickets_count": pending_tickets_count,
    })


TIER_NAMES_AR = {"Gold": "ذهبي", "Silver": "فضي", "Bronze": "برونزي"}


def _dashboard_stats_and_customers(config):
    """Numbers at the top of the dashboard, and every member with their points and tier."""
    from django.db.models import Sum, Q
    from django.utils import timezone
    from .models import CustomerProfile, TableCall, StaffShift, WhatsAppMessage
    now = timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "members": CustomerProfile.objects.count(),
        "new_members": CustomerProfile.objects.filter(created_at__gte=month_start).count(),
        "calls_today": TableCall.objects.filter(created_at__gte=day_start).count(),
        "on_shift": StaffShift.objects.filter(ended_at__isnull=True).count(),
        "whatsapp_month": WhatsAppMessage.objects.filter(direction="OUT", created_at__gte=month_start).exclude(status="failed").count(),
    }
    year_ago = now - datetime.timedelta(days=365)
    customers = CustomerProfile.objects.annotate(
        earned_year=Sum("points_transactions__points",
                        filter=Q(points_transactions__transaction_type="EARNED", points_transactions__created_at__gte=year_ago))
    ).order_by("-created_at")
    rows = []
    for c in customers:
        earned = c.earned_year or 0
        tier = "Gold" if earned >= config.gold_tier_threshold else "Silver" if earned >= config.silver_tier_threshold else "Bronze"
        rows.append({"customer": c, "earned_year": earned, "tier": tier, "tier_ar": TIER_NAMES_AR[tier]})
    return stats, rows


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
    if not config.wa_verify_token:
        config.wa_verify_token = secrets.token_urlsafe(24)
        config.save(update_fields=["wa_verify_token"])

    staff_users = StaffUser.objects.all().order_by("-role", "employee_name")

    from accounts.models import LoyaltyReward
    return render(request, "accounts/settings.html", {
        "staff_name": request.session.get("staff_name"),
        "staff_role": request.session.get("staff_role"),
        "config": config,
        "staff_users": staff_users,
        "rewards": LoyaltyReward.objects.all(),
        "loyverse_webhook_url": request.build_absolute_uri(f"/integrations/loyverse-webhook/?key={loyverse_webhook_key()}"),
        "current_network_ip": network_ip(request),
        "whatsapp_webhook_url": f"https://{request.get_host()}/api/whatsapp/webhook/",
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
    from accounts.models import ContactMessage, StaffUser, CafeConfiguration
    from django.db.models import F

    try:
        user = StaffUser.objects.get(id=staff_user_id)
    except StaffUser.DoesNotExist:
        return redirect("accounts:staff_login_view")

    # Only items that are on the customer menu, in the order customers see them
    menu_items = MenuItem.objects.filter(custom_section__isnull=False).order_by(
        "custom_section__tab__order_index", "custom_section__order_index", "custom_section_id", "order_index"
    ).annotate(
        loyverse_name=F("pos_name"),
        status_override=F("status"),
        group_name=F("custom_section__name_ar"),
    )

    messages_list = ContactMessage.objects.all().order_by("-created_at")[:20]
    unread_count = ContactMessage.objects.filter(is_read=False).count()

    # Calls still waiting for someone, plus this waiter's own calls not yet confirmed at the table
    from django.db.models import Q
    from django.utils import timezone
    from .models import TableCall
    recent = TableCall.objects.filter(created_at__gte=timezone.now() - datetime.timedelta(hours=3), arrived_at__isnull=True)
    open_calls = [_call_json(c) for c in recent.filter(Q(accepted_at__isnull=True) | Q(accepted_by=user))
                  .select_related("accepted_by").order_by("created_at")]

    return render(request, "accounts/staff_app.html", {
        "user": user,
        "menu_items": menu_items,
        "out_of_stock_count": sum(1 for i in menu_items if i.status == "OUT_OF_STOCK"),
        "messages_list": messages_list,
        "unread_count": unread_count,
        "open_calls": open_calls,
        "open_shift": user.shifts.filter(ended_at__isnull=True).first(),
        "arrival_qr_required": bool(getattr(CafeConfiguration.objects.first(), "arrival_qr_required", False)),
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

    staff_user = StaffUser.objects.filter(id=request.session.get("staff_user_id")).first()
    if not staff_user:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

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
        return JsonResponse({"status": "error", "message": "Could not save subscription"}, status=500)


@csrf_exempt
@require_POST
def staff_push_unsubscribe(request):
    """Stops call notifications on this phone."""
    from .models import StaffPushSubscription
    staff_user = StaffUser.objects.filter(id=request.session.get("staff_user_id")).first()
    if not staff_user:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    endpoint = _request_data(request).get("endpoint")
    if endpoint:
        StaffPushSubscription.objects.filter(endpoint=endpoint, staff_user=staff_user).delete()
    return JsonResponse({"status": "success"})


@csrf_exempt
@require_POST
def staff_forgot_password_request(request):
    """
    Sends a WhatsApp code to the phone saved on a staff account. The answer is
    the same whether or not the account exists, so usernames can't be probed.
    """
    username = (request.POST.get("username") or "").strip()
    if hit_limit(f"staff-forgot-ip:{client_ip(request)}", 5, 900) or hit_limit(f"staff-forgot:{username.lower()}", 3, 900):
        return JsonResponse({"status": "error", "message": "محاولات كثيرة، حاول بعد ربع ساعة"}, status=429)
    reply = {"status": "success", "message": "إذا كان للحساب رقم واتساب مسجّل، وصلك رمز عليه"}

    staff = StaffUser.objects.filter(username__iexact=username, is_active=True).first()
    if not staff or not staff.phone_number:
        return JsonResponse(reply)
    otp_code = "".join(secrets.choice("0123456789") for _ in range(6))
    request.session["staff_reset"] = {
        "id": staff.id,
        "otp": make_password(otp_code),
        "expires": (datetime.datetime.now() + datetime.timedelta(minutes=5)).timestamp(),
        "attempts": 0,
    }
    WhatsAppClient.send_otp(staff.phone_number, otp_code)
    return JsonResponse(reply)


@csrf_exempt
@require_POST
def staff_forgot_password_reset(request):
    """Checks the WhatsApp code and sets the staff member's new password."""
    pending = request.session.get("staff_reset")
    otp = (request.POST.get("otp") or "").strip()
    new_password = request.POST.get("new_password") or ""
    if not pending or datetime.datetime.now().timestamp() > pending["expires"]:
        request.session.pop("staff_reset", None)
        return JsonResponse({"status": "error", "message": "انتهت صلاحية الرمز، اطلب رمز جديد"}, status=400)
    if len(new_password) < 8:
        return JsonResponse({"status": "error", "message": "كلمة المرور لازم تكون 8 أحرف أو أكثر"}, status=400)
    pending["attempts"] += 1
    request.session["staff_reset"] = pending
    if pending["attempts"] > 5:
        request.session.pop("staff_reset", None)
        return JsonResponse({"status": "error", "message": "محاولات كثيرة، اطلب رمز جديد"}, status=429)
    if not check_password(otp, pending["otp"]):
        return JsonResponse({"status": "error", "message": "الرمز غير صحيح"}, status=400)

    staff = StaffUser.objects.filter(id=pending["id"], is_active=True).first()
    request.session.pop("staff_reset", None)
    if not staff:
        return JsonResponse({"status": "error", "message": "الحساب غير موجود"}, status=404)
    staff.password_hash = make_password(new_password)
    staff.save(update_fields=["password_hash"])
    reset_limit(f"login:staff:{staff.username.lower()}")
    return JsonResponse({"status": "success", "message": f"تم تغيير كلمة المرور. اسم المستخدم: {staff.username}"})


def staff_service_worker(request):
    """Serves the staff app service worker from /staff/ so it controls the staff pages."""
    path = os.path.join(os.path.dirname(__file__), "staff_sw.js")
    with open(path, encoding="utf-8") as f:
        response = HttpResponse(f.read(), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


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


def _notify_call_taken(call, staff):
    """Tell the table its waiter is coming, and tell other staff phones to drop the call."""
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(f"table_{call.table_number}", {
            "type": "broadcast_call_accepted", "table": call.table_number, "waiter_name": staff.employee_name,
        })
        async_to_sync(layer.group_send)("staff_dashboard", {
            "type": "broadcast_call_taken", "call_id": call.id, "table": call.table_number,
            "waiter_name": staff.employee_name, "staff_id": staff.id,
        })
    except Exception as e:
        logger.error(f"Could not broadcast accepted call: {e}")


def _call_json(call):
    def secs(a, b):
        return int((b - a).total_seconds()) if a and b else None
    return {
        "id": call.id,
        "table": call.table_number,
        "type": call.call_type,
        "created_at": call.created_at.isoformat(),
        "waiter": call.accepted_by.employee_name if call.accepted_by else None,
        "staff_id": call.accepted_by_id,
        "accept_seconds": secs(call.created_at, call.accepted_at),
        "arrive_seconds": secs(call.accepted_at, call.arrived_at),
        "arrived": call.arrived_at is not None,
    }


@csrf_exempt
@require_POST
def staff_table_calls_accept(request):
    """
    A waiter takes a table call. The first one wins; the call is logged with who and when.
    """
    from django.utils import timezone
    from .models import TableCall
    staff = StaffUser.objects.filter(id=request.session.get("staff_user_id"), is_active=True).first()
    if not staff:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    data = _request_data(request)
    table_num = str(data.get("table_number", ""))
    call_id = data.get("call_id")
    calls = TableCall.objects.filter(accepted_at__isnull=True)
    calls = calls.filter(id=call_id) if call_id else calls.filter(table_number=table_num)
    call = calls.order_by("created_at").first()
    if not call:
        taken = TableCall.objects.filter(id=call_id).select_related("accepted_by").first() if call_id else None
        who = taken.accepted_by.employee_name if taken and taken.accepted_by else "موظف آخر"
        return JsonResponse({"status": "error", "message": f"النداء أخذه {who}"}, status=409)

    # Only one waiter can win the same call
    now = timezone.now()
    if not TableCall.objects.filter(id=call.id, accepted_at__isnull=True).update(accepted_by=staff, accepted_at=now):
        return JsonResponse({"status": "error", "message": "النداء أخذه موظف آخر"}, status=409)
    # Any other open calls from the same table are handled by the same visit
    TableCall.objects.filter(table_number=call.table_number, accepted_at__isnull=True).update(accepted_by=staff, accepted_at=now)
    LIVE_TABLE_CALLS.pop(call.table_number, None)
    call.refresh_from_db()
    _notify_call_taken(call, staff)
    return JsonResponse({"status": "success", "call": _call_json(call)})


@csrf_exempt
@require_POST
def staff_table_calls_arrived(request):
    """The waiter who took the call confirms they reached the table."""
    from django.utils import timezone
    from .models import TableCall
    staff_id = request.session.get("staff_user_id")
    if not staff_id:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    data = _request_data(request)
    call = TableCall.objects.filter(id=data.get("call_id"), accepted_by_id=staff_id).first()
    if not call:
        return JsonResponse({"status": "error", "message": "Call not found"}, status=404)

    from .models import CafeConfiguration
    config = CafeConfiguration.objects.first()
    if config and config.arrival_qr_required:
        # The table's QR code holds its link, e.g. https://whitebirdcafe.com/table/7/
        import re
        match = re.search(r"/table/([^/?#]+)", str(data.get("scanned", "")))
        if not match or match.group(1) != call.table_number:
            return JsonResponse({"status": "error", "message": f"امسح كود طاولة {call.table_number}"}, status=400)
    TableCall.objects.filter(table_number=call.table_number, accepted_by_id=staff_id,
                             arrived_at__isnull=True).update(arrived_at=timezone.now())
    return JsonResponse({"status": "success"})


def _shift_json(shift):
    from django.utils import timezone
    end = shift.ended_at or timezone.now()
    return {
        "id": shift.id,
        "staff": shift.staff.employee_name,
        "started_at": shift.started_at.isoformat(),
        "ended_at": shift.ended_at.isoformat() if shift.ended_at else None,
        "minutes": int((end - shift.started_at).total_seconds() // 60),
    }


@csrf_exempt
@require_POST
def staff_shift(request):
    """Start or end the logged-in staff member's shift, only from the cafe Wi-Fi."""
    from django.utils import timezone
    from .models import CafeConfiguration, StaffShift
    staff = StaffUser.objects.filter(id=request.session.get("staff_user_id"), is_active=True).first()
    if not staff:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    if not on_cafe_network(request, CafeConfiguration.objects.first()):
        return JsonResponse({"status": "error", "code": "not_cafe_network", "can_approve": staff.can_approve_network or staff.role == "ADMIN",
                             "message": "لازم تكون شابك على واي فاي الكافيه"}, status=403)

    action = _request_data(request).get("action")
    open_shift = StaffShift.objects.filter(staff=staff, ended_at__isnull=True).first()
    if action == "start":
        if open_shift:
            return JsonResponse({"status": "error", "message": "الدوام مبلّش أصلاً"}, status=400)
        shift = StaffShift.objects.create(staff=staff, start_ip=network_ip(request))
    elif action == "end":
        if not open_shift:
            return JsonResponse({"status": "error", "message": "ما في دوام مفتوح"}, status=400)
        open_shift.ended_at = timezone.now()
        open_shift.end_ip = network_ip(request)
        open_shift.save()
        shift = open_shift
    else:
        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)
    return JsonResponse({"status": "success", "shift": _shift_json(shift)})


@require_GET
def staff_shifts_log(request):
    """Shifts of the last 7 days, with hours per staff member (admin dashboard)."""
    from django.utils import timezone
    from .models import StaffShift
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    since = timezone.now() - datetime.timedelta(days=7)
    shifts = [_shift_json(s) for s in StaffShift.objects.filter(started_at__gte=since).select_related("staff")[:300]]
    totals = {}
    for s in shifts:
        totals[s["staff"]] = totals.get(s["staff"], 0) + s["minutes"]
    return JsonResponse({
        "status": "success",
        "shifts": shifts,
        "on_shift": [s["staff"] for s in shifts if not s["ended_at"]],
        "totals": [{"staff": k, "minutes": v} for k, v in sorted(totals.items(), key=lambda kv: -kv[1])],
    })


@csrf_exempt
@require_POST
def staff_cafe_network(request):
    """
    From inside the cafe: save this connection's address as the cafe Wi-Fi.
    The manager can also clear it or undo the last change; staff marked
    can_approve_network may only add the current address, and the manager is notified.
    """
    from django.utils import timezone
    from .models import CafeConfiguration
    staff = StaffUser.objects.filter(id=request.session.get("staff_user_id"), is_active=True).first()
    is_admin = staff_is_admin(request)
    if not staff or not (is_admin or staff.can_approve_network):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    action = _request_data(request).get("action")
    if action in ("clear", "undo") and not is_admin:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    before = config.cafe_network_ips
    if action == "clear":
        config.cafe_network_ips = ""
    elif action == "undo":
        config.cafe_network_ips = config.cafe_network_previous
    else:
        ips = [ip for ip in config.cafe_network_ips.splitlines() if ip.strip()]
        current = network_ip(request)
        if current not in ips:
            ips.append(current)
        config.cafe_network_ips = "\n".join(ips[-5:])  # keep the last few addresses the provider gave
    if config.cafe_network_ips != before:
        config.cafe_network_previous = before
        config.cafe_network_changed_by = staff.employee_name
        config.cafe_network_changed_at = timezone.now()
    config.save(update_fields=["cafe_network_ips", "cafe_network_previous", "cafe_network_changed_by", "cafe_network_changed_at"])

    if config.cafe_network_ips != before and not is_admin:
        try:
            from .push_notifications import send_web_push_to_staff
            send_web_push_to_staff("📶 تغيّرت شبكة الكافيه", f"{staff.employee_name} اعتمد شبكة الواي فاي الحالية. إذا مش صح، رجّعها من الإعدادات ← الموظفين.",
                                   url="/staff/settings/", tag="cafe-network", admins_only=True)
        except Exception as e:
            logger.error(f"Cafe network push failed: {e}")
    return JsonResponse({"status": "success", "ips": config.cafe_network_ips.splitlines(), "current": network_ip(request),
                         "changed_by": config.cafe_network_changed_by})


@require_GET
def staff_table_calls_log(request):
    """Today's calls with response times, plus averages per waiter (admin dashboard)."""
    from django.utils import timezone
    from .models import TableCall
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    calls = list(TableCall.objects.filter(created_at__gte=start).select_related("accepted_by")[:300])
    rows = [_call_json(c) for c in calls]

    per_waiter = {}
    for r in rows:
        if not r["waiter"]:
            continue
        w = per_waiter.setdefault(r["waiter"], {"waiter": r["waiter"], "calls": 0, "accept": [], "arrive": []})
        w["calls"] += 1
        w["accept"].append(r["accept_seconds"])
        if r["arrive_seconds"] is not None:
            w["arrive"].append(r["arrive_seconds"])
    waiters = [{
        "waiter": w["waiter"], "calls": w["calls"],
        "avg_accept": round(sum(w["accept"]) / len(w["accept"])),
        "avg_arrive": round(sum(w["arrive"]) / len(w["arrive"])) if w["arrive"] else None,
        "not_confirmed": w["calls"] - len(w["arrive"]),
    } for w in per_waiter.values()]
    return JsonResponse({"status": "success", "calls": rows, "waiters": sorted(waiters, key=lambda w: -w["calls"])})



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
    
    maps_link = (request.POST.get("maps_link") or "").strip()
    if maps_link:
        coords = coordinates_from_maps_link(maps_link)
        if not coords:
            return JsonResponse({"status": "error", "message": "ما قدرنا نقرأ الموقع من الرابط. امسح الرابط، واكتب خط العرض والطول بالخانات تحت (بخرائط جوجل اضغط مطولاً على مكان الكافيه وانسخ الرقمين)"}, status=400)
        config.latitude, config.longitude = coords
    try:
        if not maps_link and request.POST.get("latitude"):
            config.latitude = float(request.POST.get("latitude"))
        if not maps_link and request.POST.get("longitude"):
            config.longitude = float(request.POST.get("longitude"))
    except (ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "خط العرض أو الطول لازم يكون رقم"}, status=400)

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
    config.wa_phone_number_id = request.POST.get("wa_phone_number_id", config.wa_phone_number_id).strip()
    config.wa_business_account_id = request.POST.get("wa_business_account_id", config.wa_business_account_id).strip()
    # Secrets are never sent back to the page, so an empty field keeps the saved value
    if request.POST.get("wa_access_token", "").strip():
        config.wa_access_token = request.POST["wa_access_token"].strip()
    if request.POST.get("wa_app_secret", "").strip():
        config.wa_app_secret = request.POST["wa_app_secret"].strip()
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
    if request.POST.get("birthday_settings"):
        config.birthday_messages_enabled = request.POST.get("birthday_messages_enabled") == "on"

    # Launch Promotion settings
    config.featured_item_is_active = request.POST.get("featured_item_is_active") == "on"
    config.arrival_qr_required = request.POST.get("arrival_qr_required") == "on"
    if "table_count" in request.POST:
        config.table_calls_wifi_only = request.POST.get("table_calls_wifi_only") == "on"
        try:
            config.table_count = max(1, min(200, int(request.POST.get("table_count") or config.table_count)))
        except ValueError:
            return JsonResponse({"status": "error", "message": "عدد الطاولات لازم يكون رقم"}, status=400)
        hookah_id = request.POST.get("hookah_staff") or None
        config.hookah_staff = StaffUser.objects.filter(id=hookah_id, is_active=True).first() if hookah_id else None
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

    message_id = _request_data(request).get("message_id")
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
    Table services page, opened from the table's QR code: /table/<n>/?auth=<token>.
    Without the code's token, or off the cafe Wi-Fi (when required), it explains instead.
    """
    from .models import CafeConfiguration
    from .security import table_token_is_valid
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    auth_token = request.GET.get("auth", "")
    if not table_token_is_valid(config, table_number, auth_token):
        table_status = "no_code"
    elif config.table_calls_wifi_only and not on_cafe_network(request, config):
        table_status = "no_wifi"
    else:
        table_status = "ok"
    return render(request, "accounts/table_service.html", {
        "table_number": table_number,
        "auth_token": auth_token if table_status == "ok" else "",
        "table_status": table_status,
        "wifi_ssid": config.wifi_ssid,
        "wifi_password": config.wifi_password,
    })


def staff_table_qr_view(request):
    """Printable sheet with a QR code for every table (manager only)."""
    from .models import CafeConfiguration
    from .security import table_token
    if not staff_is_admin(request):
        return redirect("accounts:staff_login_view")
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    base = request.build_absolute_uri("/").rstrip("/")
    import segno
    from django.utils.safestring import mark_safe
    tables = []
    for n in range(1, config.table_count + 1):
        url = f"{base}/table/{n}/?auth={table_token(config, n)}"
        qr_svg = mark_safe(segno.make(url, error="m").svg_inline(border=2, omitsize=True))
        tables.append({"number": n, "url": url, "qr_svg": qr_svg})
    return render(request, "accounts/table_qr.html", {"tables": tables, "config": config})


@csrf_exempt
@require_POST
def staff_table_qr_rotate(request):
    """New secret for the table codes: every printed code stops working and must be reprinted."""
    import secrets
    from .models import CafeConfiguration
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    config.table_qr_secret = secrets.token_urlsafe(18)
    config.save(update_fields=["table_qr_secret"])
    return JsonResponse({"status": "success"})

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
        "message": f"{item.display_name or item.pos_name}: " + ("متوفر ✓" if item.status != "OUT_OF_STOCK" else "خلص، ومخفي عن المنيو"),
        "new_status": item.status,
        "item_id": item.id
    })


@require_GET
def get_whatsapp_logs(request):
    """
    Returns the latest WhatsApp messages sent and received, with Meta's delivery status.
    """
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from .models import WhatsAppMessage
    from django.utils import timezone
    logs = [{
        "phone_number": m.phone,
        "direction": m.direction,
        "type": m.kind,
        "message": m.text,
        "status": m.status,
        "error": m.error,
        "time": timezone.localtime(m.created_at).strftime("%Y-%m-%d %H:%M"),
    } for m in WhatsAppMessage.objects.all()[:50]]
    return JsonResponse({"status": "success", "logs": logs})

@csrf_exempt
@require_POST
def whatsapp_test_send(request):
    """
    Sends the welcome template to a test number, to check the connection end to end.
    """
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    phone_number = request.POST.get("phone_number")
    if not phone_number:
        return JsonResponse({"status": "error", "message": "أدخل رقم الهاتف"}, status=400)
    if not WhatsAppClient.is_configured():
        return JsonResponse({"status": "error", "message": "احفظ بيانات الواتساب أولاً"}, status=400)

    if WhatsAppClient.send_welcome_loyalty(phone_number, "تجربة"):
        return JsonResponse({"status": "success", "message": "تم الإرسال، تابع حالة الرسالة بالسجل"})
    from .models import WhatsAppMessage
    last = WhatsAppMessage.objects.filter(direction="OUT").first()
    return JsonResponse({"status": "error", "message": f"فشل الإرسال: {last.error if last else ''}"}, status=502)


@require_GET
def whatsapp_templates_status(request):
    """Approval status of the cafe's message templates on Meta."""
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    from cafe_smart.integrations import TEMPLATES
    try:
        statuses = WhatsAppClient.template_statuses()
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "success", "templates": [
        {"name": t["name"], "category": t["category"], "status": statuses.get(t["name"], "NOT_SUBMITTED")}
        for t in TEMPLATES.values()]})


@csrf_exempt
@require_POST
def whatsapp_templates_submit(request):
    """Sends the cafe's message templates to Meta for approval."""
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    try:
        result = WhatsAppClient.submit_templates()
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "success", "templates": result})


def notify_staff_new_message(msg):
    """Phone notification for a new customer message, so staff see it with the app closed."""
    try:
        from .push_notifications import send_web_push_to_staff
        send_web_push_to_staff(f"✉️ رسالة جديدة من {msg.name}", (msg.message or "")[:120], url="/staff/app/", tag=f"message-{msg.id}")
    except Exception as e:
        logger.error(f"Message push failed: {e}")


def _broadcast_contact_message(msg):
    """Shows a new message in the staff app and dashboard right away."""
    notify_staff_new_message(msg)
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)("staff_dashboard", {
            "type": "broadcast_contact_message",
            "message": {
                "id": msg.id, "name": msg.name, "contact": msg.contact, "inquiry_type": msg.inquiry_type,
                "message": msg.message, "created_at": msg.created_at.strftime("%I:%M %p"),
            },
        })


@csrf_exempt
def whatsapp_webhook(request):
    """
    Meta calls this with incoming customer messages and delivery updates.
    GET is Meta's one-time subscription check against the verify token.
    """
    import hashlib
    import hmac
    import json
    from django.http import HttpResponse, HttpResponseForbidden
    from .models import CafeConfiguration, ContactMessage, WhatsAppMessage

    config = CafeConfiguration.objects.first()
    if request.method == "GET":
        token = request.GET.get("hub.verify_token", "")
        if config and config.wa_verify_token and request.GET.get("hub.mode") == "subscribe" \
                and hmac.compare_digest(token, config.wa_verify_token):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponseForbidden("verify token mismatch")
    if request.method != "POST":
        return HttpResponse(status=405)

    if config and config.wa_app_secret:
        expected = "sha256=" + hmac.new(config.wa_app_secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            return HttpResponseForbidden("bad signature")

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"status": "ignored"})

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                phone = msg.get("from", "")
                kind = msg.get("type", "")
                if kind == "text":
                    text = msg.get("text", {}).get("body", "")
                elif kind == "button":
                    text = msg.get("button", {}).get("text", "")
                else:
                    text = f"[{kind}]"
                if WhatsAppMessage.objects.filter(wa_message_id=msg.get("id", "-")).exists():
                    continue
                WhatsAppMessage.objects.create(direction="IN", phone=phone, kind="incoming", text=text[:1000],
                                               status="received", wa_message_id=msg.get("id", ""))
                contact_msg = ContactMessage.objects.create(name=names.get(phone) or phone, contact=f"+{phone}",
                                                            inquiry_type="WhatsApp", message=text)
                _broadcast_contact_message(contact_msg)
            for st in value.get("statuses", []):
                errors = st.get("errors") or [{}]
                WhatsAppMessage.objects.filter(wa_message_id=st.get("id", "-")).update(
                    status=st.get("status", ""),
                    error=(errors[0].get("error_data", {}).get("details") or errors[0].get("title") or "")[:500]
                    if st.get("status") == "failed" else "")
    return JsonResponse({"status": "ok"})

MAPS_HOSTS = ("maps.app.goo.gl", "goo.gl", "google.com", "maps.google.com", "www.google.com", "google.jo", "www.google.jo",
              "maps.google.jo", "consent.google.com")


def _coordinates_in(text):
    """Finds lat,lng in a Google Maps URL or page: the place pin (!3d…!4d…) first, then @lat,lng,
    q=lat,lng or the map centre (center=lat,lng, or [[[zoom,lng,lat] in the page's start-up data)."""
    import re
    from urllib.parse import unquote
    text = unquote(text.replace("&amp;", "&"))
    for pattern, swap in ((r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", False), (r"@(-?\d+\.\d+),(-?\d+\.\d+)", False),
                          (r"[?&](?:q|query|ll|destination|center|markers)=(-?\d+\.\d+),\s*(-?\d+\.\d+)", False),
                          (r"\[\[\[\d+(?:\.\d+)?,(-?\d+\.\d+),(-?\d+\.\d+)\]", True)):
        match = re.search(pattern, text)
        if match:
            lat, lng = float(match.group(1)), float(match.group(2))
            if swap:
                lat, lng = lng, lat
            if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0.0, 0.0):
                return lat, lng
    return None


def coordinates_from_maps_link(link):
    """(lat, lng) from a Google Maps link, following share short links (maps.app.goo.gl) to the full URL
    and, when that has no coordinates (links shared from iPhone), reading them from the map page."""
    from urllib.parse import urlparse, parse_qs
    import requests
    url = link
    if (urlparse(url).hostname or "").lower() not in MAPS_HOSTS:
        return None
    coords = _coordinates_in(url)
    if coords:
        return coords
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept-Language": "en", "Cookie": "CONSENT=YES+; SOCS=CAI"}
    for _ in range(6):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in MAPS_HOSTS:
            logger.warning(f"Maps link left Google Maps: {url[:300]}")
            return None
        if host == "consent.google.com":
            # Cookie wall: carry on to the page it was guarding
            url = (parse_qs(parsed.query).get("continue") or [""])[0]
            continue
        try:
            res = requests.get(url, allow_redirects=False, timeout=8, headers=headers)
        except requests.RequestException as e:
            logger.warning(f"Maps link could not be opened: {e}")
            return None
        location = res.headers.get("Location")
        if not location:
            coords = _coordinates_in(res.text[:500000])
            if not coords:
                logger.warning(f"No coordinates on Maps page {url[:300]} (HTTP {res.status_code})")
            return coords
        coords = _coordinates_in(location)
        if coords:
            return coords
        url = requests.compat.urljoin(url, location)
    return None


def loyverse_webhook_key():
    """
    Secret part of the webhook URL that is registered in Loyverse.
    Derived from SECRET_KEY so nothing extra has to be configured;
    LOYVERSE_WEBHOOK_SECRET overrides it.
    """
    import hashlib
    import hmac
    override = getattr(settings, "LOYVERSE_WEBHOOK_SECRET", "")
    if override:
        return override
    return hmac.new(settings.SECRET_KEY.encode(), b"loyverse-webhook", hashlib.sha256).hexdigest()[:32]


def webhook_key_is_valid(request):
    return secrets.compare_digest(request.GET.get("key", ""), loyverse_webhook_key())


@csrf_exempt
@require_POST
def loyverse_webhook(request):
    """
    Loyverse "receipts.update" webhook. Each receipt is re-read from the Loyverse API
    (when a token is configured) so a forged request cannot add points or raffle tickets.
    Records earned points for tiers, refreshes the points balance, generates raffle
    tickets and sends the WhatsApp points message.
    """
    import json
    if not webhook_key_is_valid(request):
        return JsonResponse({"status": "error", "message": "Forbidden"}, status=403)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON payload"}, status=400)

    # Loyverse sends {"type": "receipts.update", "receipts": [...]}; older test payloads
    # used {"event_type": "receipt.created", "data": {...}}
    if isinstance(data.get("receipts"), list):
        receipts = data["receipts"]
    else:
        receipts = [data.get("data", data)]

    results = [_process_loyverse_receipt(r) for r in receipts if isinstance(r, dict)]
    return JsonResponse({"status": "success", "results": results})


def _process_loyverse_receipt(receipt_data):
    from django.db import transaction
    from django.utils import timezone
    from accounts.models import PointsTransaction, CafeConfiguration, RaffleTicket

    receipt_number = str(receipt_data.get("receipt_number") or "").strip()
    if not receipt_number:
        return {"status": "ignored", "reason": "no receipt number"}

    if LoyverseClient.is_configured():
        try:
            verified = LoyverseClient.get_receipt(receipt_number)
        except LoyverseError as e:
            logger.error(f"Webhook receipt {receipt_number} could not be verified: {e}")
            return {"receipt": receipt_number, "status": "error", "reason": "verification failed"}
        if not verified:
            return {"receipt": receipt_number, "status": "ignored", "reason": "receipt not found in Loyverse"}
        receipt_data = verified

    if receipt_data.get("receipt_type", "SALE") != "SALE" or receipt_data.get("cancelled_at"):
        return {"receipt": receipt_number, "status": "ignored", "reason": "not a completed sale"}

    loyverse_customer_id = receipt_data.get("customer_id")
    if not loyverse_customer_id:
        return {"receipt": receipt_number, "status": "ignored", "reason": "no customer"}

    customer = CustomerProfile.objects.filter(loyverse_customer_id=loyverse_customer_id).first()
    if not customer:
        return {"receipt": receipt_number, "status": "ignored", "reason": "customer not registered on the website"}

    total_amount = float(receipt_data.get("total_money", receipt_data.get("total_amount", 0)) or 0)
    points_earned = int(float(receipt_data.get("points_earned", 0) or 0))

    with transaction.atomic():
        # Loyverse fires receipts.update again when a receipt changes: process each receipt once
        if PointsTransaction.objects.filter(receipt_number=receipt_number).exists():
            return {"receipt": receipt_number, "status": "duplicate"}
        PointsTransaction.objects.create(
            customer=customer,
            points=points_earned,
            transaction_type='EARNED',
            receipt_number=receipt_number
        )

        if receipt_data.get("points_balance") is not None:
            customer.points_balance = int(float(receipt_data["points_balance"]))
        else:
            customer.points_balance += points_earned
        customer.points_synced_at = timezone.now()
        customer.monthly_spend += total_amount
        customer.save()

        # Every paid receipt of a registered member is one entry in this month's draw
        num_tickets = 1 if total_amount > 0 else 0
        for _ in range(num_tickets):
            ticket_code = f"TKT-{secrets.randbelow(900000) + 100000}"
            while RaffleTicket.objects.filter(ticket_code=ticket_code).exists():
                ticket_code = f"TKT-{secrets.randbelow(900000) + 100000}"
            RaffleTicket.objects.create(
                ticket_code=ticket_code,
                customer=customer,
                receipt_number=receipt_number,
                receipt_amount=total_amount
            )

    if points_earned > 0:
        try:
            WhatsAppClient.send_points_earned(customer.phone_number, points_earned, customer.points_balance)
        except Exception as e:
            logger.error(f"Failed to send points earned WhatsApp notification: {e}")

    return {
        "receipt": receipt_number,
        "status": "processed",
        "points_earned": points_earned,
        "total_points": customer.points_balance,
        "tickets_generated": num_tickets,
    }


@csrf_exempt
@require_POST
def customer_redeem_voucher(request):
    """
    Points are now redeemed by the cashier inside Loyverse (the loyalty page lists
    the rewards). Kept so old pages get a clear answer instead of a 404.
    """
    return JsonResponse({
        "status": "error",
        "message": "استبدال النقاط يتم عند الكاشير / Please ask the cashier to redeem your points"
    }, status=410)

@csrf_exempt
@require_POST
def staff_redeem_scan(request):
    """
    Endpoint for staff to verify and burn a customer's claimed voucher.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    data = _request_data(request)
    voucher_code = (data.get("voucher_code") or data.get("barcode") or "").strip()
    if not voucher_code:
        return JsonResponse({"status": "error", "message": "Voucher code is required"}, status=400)

    from accounts.models import CustomerVoucher, CafeConfiguration
    try:
        voucher = CustomerVoucher.objects.get(voucher_code=voucher_code)
    except CustomerVoucher.DoesNotExist:
        # Not a voucher: the staff app also scans the customer's loyalty card barcode
        customer = CustomerProfile.objects.filter(barcode=voucher_code).first()
        if not customer:
            customer = CustomerProfile.objects.filter(phone_number=clean_phone_number(voucher_code)).first()
        if customer:
            LoyverseClient.refresh_customer_points(customer, max_age_seconds=0)
            config = CafeConfiguration.objects.first()
            tier = get_customer_tier(customer, config)
            today = datetime.date.today()
            is_birthday = bool(customer.birth_date) and (customer.birth_date.month, customer.birth_date.day) == (today.month, today.day)
            birthday_gift = ""
            if is_birthday and config:
                birthday_gift = {"Gold": config.birthday_msg_gold, "Silver": config.birthday_msg_silver}.get(tier, config.birthday_msg_bronze)
            from accounts.models import LoyaltyReward
            available_rewards = [
                f"{r.name_ar} ({r.points_required})"
                for r in LoyaltyReward.objects.filter(is_active=True, points_required__lte=customer.points_balance)
            ]
            return JsonResponse({
                "status": "success",
                "type": "customer",
                "customer_name": customer.full_name,
                "points": customer.points_balance,
                "tier": tier,
                "is_birthday": is_birthday,
                "birthday_gift": birthday_gift,
                "available_rewards": available_rewards,
            })
        return JsonResponse({"status": "error", "message": "الرمز غير صحيح: لا يوجد كوبون أو بطاقة عميل بهذا الرقم"}, status=404)

    if voucher.is_redeemed:
        return JsonResponse({"status": "error", "message": "Voucher has already been redeemed"}, status=400)

    # Burn/Redeem voucher
    voucher.is_redeemed = True
    voucher.save()

    return JsonResponse({
        "status": "success",
        "type": "voucher",
        "message": f"كوبون صالح: تم تأكيد استلام العميل لـ ({voucher.reward_details}) بنجاح!",
        "customer_name": voucher.customer.full_name,
        "reward": voucher.reward_details
    })

@csrf_exempt
@require_POST
def staff_raffle_draw(request):
    """Draws this month's (or the chosen month's) three winners and messages them on WhatsApp."""
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized. Admin rights required."}, status=401)
    from .draws import get_draw, parse_month, run_draw, draw_info
    data = _request_data(request)
    draw = get_draw(parse_month(data.get("month")))
    try:
        draw = run_draw(draw)
    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    info = draw_info(draw)
    first = info["winners"][0] if info["winners"] else {}
    return JsonResponse({
        "status": "success",
        "message": "تم السحب",
        "draw": info,
        # Older screens read the first winner from these
        "winner_name": first.get("name", ""),
        "winner_phone": first.get("phone", ""),
        "ticket_code": first.get("ticket", ""),
    })


def staff_raffle_month(request):
    """Entries, first and last receipt, prizes and winners of one month's draw."""
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    from .draws import get_draw, parse_month, draw_info
    return JsonResponse({"status": "success", "draw": draw_info(get_draw(parse_month(request.GET.get("month"))))})


@csrf_exempt
@require_POST
def staff_raffle_prizes(request):
    """Saves the three prizes (name and picture) of one month's draw."""
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    from .draws import get_draw, parse_month, draw_info
    draw = get_draw(parse_month(request.POST.get("month")))
    fields = []
    for place in (1, 2, 3):
        name = request.POST.get(f"prize{place}_name")
        if name is not None:
            setattr(draw, f"prize{place}_name", name.strip()[:255])
            fields.append(f"prize{place}_name")
        try:
            image = _uploaded_image_url(request, f"prize{place}_file", "prizes")
        except ValueError as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
        if image:
            setattr(draw, f"prize{place}_image", image)
            fields.append(f"prize{place}_image")
        elif request.POST.get(f"prize{place}_remove_image") == "1":
            setattr(draw, f"prize{place}_image", "")
            fields.append(f"prize{place}_image")
    if fields:
        draw.save(update_fields=fields)
    return JsonResponse({"status": "success", "message": "انحفظت الجوائز", "draw": draw_info(draw)})


def _uploaded_image_url(request, field, folder):
    """Save an uploaded image to media storage and return its URL, or None."""
    from django.core.files.storage import default_storage
    upload = request.FILES.get(field)
    if not upload:
        return None
    if not (upload.content_type or "").startswith("image/") or upload.size > 8 * 1024 * 1024:
        raise ValueError("الملف لازم يكون صورة وحجمها أقل من 8 ميغا")
    return default_storage.url(default_storage.save(f"{folder}/{upload.name}", upload))


@csrf_exempt
@require_POST
def staff_tab_manage(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuTab
    action = request.POST.get("action")
    try:
        uploaded = _uploaded_image_url(request, "image_file", "menu/tabs")
    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    
    if action == "create":
        name_en = request.POST.get("name_en")
        name_ar = request.POST.get("name_ar")
        image = uploaded or request.POST.get("image", "")
        order_index = int(request.POST.get("order_index", 0))
        
        tab = MenuTab.objects.create(name_en=name_en, name_ar=name_ar, image=image, order_index=order_index)
        return JsonResponse({"status": "success", "message": "Tab created successfully!", "tab_id": tab.id})
        
    elif action == "update":
        tab_id = request.POST.get("tab_id")
        tab = MenuTab.objects.get(id=tab_id)
        tab.name_en = request.POST.get("name_en", tab.name_en)
        tab.name_ar = request.POST.get("name_ar", tab.name_ar)
        tab.image = uploaded or request.POST.get("image", tab.image)
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
    try:
        uploaded = _uploaded_image_url(request, "banner_file", "menu/groups")
    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    
    if action == "create":
        tab_id = request.POST.get("tab_id")
        tab = MenuTab.objects.get(id=tab_id)
        name_en = request.POST.get("name_en")
        name_ar = request.POST.get("name_ar")
        banner_image = uploaded or request.POST.get("banner_image", "")
        order_index = int(request.POST.get("order_index", 0))
        
        section = MenuSection.objects.create(tab=tab, name_en=name_en, name_ar=name_ar, banner_image=banner_image, order_index=order_index)
        return JsonResponse({"status": "success", "message": "Group created successfully!", "section_id": section.id})
        
    elif action == "update":
        section_id = request.POST.get("section_id")
        section = MenuSection.objects.get(id=section_id)
        section.name_en = request.POST.get("name_en", section.name_en)
        section.name_ar = request.POST.get("name_ar", section.name_ar)
        section.banner_image = uploaded or request.POST.get("banner_image", section.banner_image)
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
    data = _request_data(request)
    section_id = data.get("section_id")
    if hasattr(data, "getlist"):
        item_ids = data.getlist("item_ids[]") or data.getlist("item_ids")
    else:
        item_ids = data.get("item_ids") or []

    section = MenuSection.objects.filter(id=section_id).first() if section_id else None
    if not section or not item_ids:
        return JsonResponse({"status": "error", "message": "اختر الصنف والمجموعة أولاً"}, status=400)
    MenuItem.objects.filter(id__in=item_ids).update(custom_section=section)
    
    return JsonResponse({"status": "success", "message": "Items assigned to group successfully!"})


@csrf_exempt
@require_POST
def staff_item_remove_group(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    from menu.models import MenuItem
    item_id = _request_data(request).get("item_id")

    item = MenuItem.objects.filter(id=item_id).first() if item_id else None
    if not item:
        return JsonResponse({"status": "error", "message": "Item not found"}, status=404)
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

    if hit_limit(f"otp:phone:{phone_number}", 3, 3600) or hit_limit(f"otp:ip:{client_ip(request)}", 10, 3600):
        return JsonResponse({
            "status": "error",
            "message": "تم طلب رموز كثيرة، حاول بعد ساعة / Too many codes requested, try again in an hour."
        }, status=429)
    
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
    if not WhatsAppClient.send_otp(phone_number, otp_code) and WhatsAppClient.is_configured():
        return JsonResponse({"status": "error", "message": "تعذر إرسال رمز التحقق على الواتساب / Could not send the WhatsApp code"}, status=502)
    
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
    if len(new_password) < MIN_CUSTOMER_PASSWORD_LENGTH:
        return JsonResponse({"status": "error", "message": "كلمة المرور يجب أن تكون 6 أحرف على الأقل / Password must be at least 6 characters"}, status=400)
        
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
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import CafeConfiguration
    from menu.models import MenuCategory, MenuItem, MenuSection
    import requests

    token = LoyverseClient.get_token()

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
        def fetch_all(resource):
            """Loyverse pages its lists (50 by default), so follow the cursor."""
            results, cursor = [], None
            while True:
                params = {"limit": 250}
                if cursor:
                    params["cursor"] = cursor
                res = requests.get(f"https://api.loyverse.com/v1.0/{resource}", headers=headers, params=params, timeout=15)
                if res.status_code == 401:
                    raise PermissionError("invalid token")
                res.raise_for_status()
                data = res.json()
                results.extend(data.get(resource, []))
                cursor = data.get("cursor")
                if not cursor:
                    return results

        synced_cats = 0
        categories_data = fetch_all("categories")
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
        synced_items = 0
        valid_for_sale_item_ids = []
        items_data = fetch_all("items")
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
                if not existing_item.price_locked:
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

                MenuItem.objects.create(
                    loyverse_id=item_id,
                    pos_name=item_name,
                    display_name=item_name,
                    price=price,
                    category=category,
                    custom_section=None,  # waits in "pending" until staff place it
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
    except PermissionError:
        return JsonResponse({
            "status": "error",
            "message": "رمز الوصول لـ Loyverse غير صحيح أو منتهي. أنشئ رمزاً جديداً من Loyverse وأدخله في الإعدادات."
        }, status=400)
    except Exception as e:
        logger.error(f"Loyverse live sync failed: {e}")
        return JsonResponse({
            "status": "error",
            "message": "فشل الاتصال بخوادم Loyverse. تأكد من رمز الوصول وحاول مرة أخرى."
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
    phone_number = clean_phone_number(request.POST.get("phone_number", "").strip())
    can_approve_network = request.POST.get("can_approve_network") in ("on", "true", "1")

    if not username or not employee_name:
        return JsonResponse({"status": "error", "message": "اسم المستخدم واسم الموظف مطلوبان"}, status=400)
    if role not in ("ADMIN", "WAITER"):
        return JsonResponse({"status": "error", "message": "صلاحية غير صحيحة"}, status=400)
    if password and len(password) < 8:
        return JsonResponse({"status": "error", "message": "كلمة مرور الموظف يجب أن تكون 8 أحرف على الأقل"}, status=400)

    if user_id:
        try:
            user = StaffUser.objects.get(id=user_id)
            user.username = username
            user.employee_name = employee_name
            user.role = role
            user.phone_number = phone_number
            user.can_approve_network = can_approve_network
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
            phone_number=phone_number,
            can_approve_network=can_approve_network,
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
    if len(new_password) < 8:
        return JsonResponse({"status": "error", "message": "كلمة مرور الموظف يجب أن تكون 8 أحرف على الأقل"}, status=400)

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


@csrf_exempt
@require_POST
def staff_rewards_manage(request):
    """
    Admin: create, update or delete the rewards shown on the customer loyalty page.
    """
    if not staff_is_admin(request):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    from accounts.models import LoyaltyReward
    action = request.POST.get("action")

    if action == "delete":
        LoyaltyReward.objects.filter(id=request.POST.get("reward_id")).delete()
        return JsonResponse({"status": "success", "message": "تم حذف المكافأة"})

    if action not in ("create", "update"):
        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)

    if action == "update":
        reward = LoyaltyReward.objects.filter(id=request.POST.get("reward_id")).first()
        if not reward:
            return JsonResponse({"status": "error", "message": "Reward not found"}, status=404)
    else:
        reward = LoyaltyReward()

    name_ar = (request.POST.get("name_ar") or "").strip()
    if not name_ar:
        return JsonResponse({"status": "error", "message": "اسم المكافأة بالعربي مطلوب"}, status=400)
    try:
        points_required = int(request.POST.get("points_required", ""))
        order_index = int(request.POST.get("order_index") or 0)
    except ValueError:
        return JsonResponse({"status": "error", "message": "عدد النقاط يجب أن يكون رقماً"}, status=400)
    if points_required <= 0:
        return JsonResponse({"status": "error", "message": "عدد النقاط يجب أن يكون أكبر من صفر"}, status=400)

    reward.name_ar = name_ar
    reward.name_en = (request.POST.get("name_en") or "").strip()
    reward.points_required = points_required
    reward.order_index = order_index
    reward.is_active = request.POST.get("is_active", "on") == "on"
    if request.POST.get("clear_image") == "on":
        reward.image = None
    elif "image" in request.FILES:
        reward.image = request.FILES["image"]
    reward.save()
    return JsonResponse({"status": "success", "message": "تم حفظ المكافأة", "reward_id": reward.id})


def _offer_audience(config, tier):
    rows = [r for r in _dashboard_stats_and_customers(config)[1] if r["customer"].marketing_opt_in]
    return [r for r in rows if not tier or r["tier"] == tier]


def staff_offers_view(request):
    """Send an offer on WhatsApp to the members who agreed to offers (manager only)."""
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return redirect("accounts:staff_login_view")
    from .models import CafeConfiguration, CustomerProfile, WhatsAppMessage
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    audience = _offer_audience(config, "")
    counts = {"all": len(audience)}
    for tier in ("Gold", "Silver", "Bronze"):
        counts[tier] = sum(1 for r in audience if r["tier"] == tier)
    return render(request, "accounts/offers.html", {
        "config": config,
        "counts": counts,
        "members_total": CustomerProfile.objects.count(),
        "recent_offers": WhatsAppMessage.objects.filter(kind="offer")[:30],
    })


@csrf_exempt
@require_POST
def staff_offers_send(request):
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    import threading
    from decimal import Decimal, InvalidOperation
    from .models import CafeConfiguration
    config = CafeConfiguration.objects.first() or CafeConfiguration.objects.create()
    data = _request_data(request)
    if data.get("price") not in (None, ""):
        try:
            config.wa_marketing_price = Decimal(str(data.get("price")))
            config.save(update_fields=["wa_marketing_price"])
        except InvalidOperation:
            return JsonResponse({"status": "error", "message": "سعر الرسالة لازم يكون رقم"}, status=400)
    if data.get("action") == "save_price":
        return JsonResponse({"status": "success", "message": "انحفظ السعر"})
    text = (data.get("text") or "").strip()
    if len(text) < 5:
        return JsonResponse({"status": "error", "message": "اكتب نص العرض"}, status=400)
    if len(text) > 500:
        return JsonResponse({"status": "error", "message": "نص العرض طويل، خليه أقل من 500 حرف"}, status=400)
    recipients = [(r["customer"].phone_number, r["customer"].full_name) for r in _offer_audience(config, data.get("tier") or "")]
    if not recipients:
        return JsonResponse({"status": "error", "message": "ما في أعضاء موافقين على العروض بهالمجموعة"}, status=400)

    def send_all():
        from django.db import connection
        try:
            for phone, name in recipients:
                try:
                    WhatsAppClient.send_offer(phone, name, text)
                except Exception as e:
                    logger.error(f"Offer to {phone} failed: {e}")
        finally:
            connection.close()

    threading.Thread(target=send_all, daemon=True).start()
    return JsonResponse({"status": "success", "message": f"بدأ إرسال العرض لـ {len(recipients)} عضو"})


@csrf_exempt
@require_POST
def customer_marketing_opt_in(request):
    """The member turns birthday gifts and offers on WhatsApp on or off."""
    customer_id = request.session.get("customer_id")
    if not customer_id:
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    data = _request_data(request)
    agreed = str(data.get("opt_in", "")).lower() in ("1", "true", "on")
    CustomerProfile.objects.filter(id=customer_id).update(marketing_opt_in=agreed)
    return JsonResponse({"status": "success", "opt_in": agreed})
