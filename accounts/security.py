import ipaddress

from django.core.cache import cache


def client_ip(request):
    """
    Visitor IP for rate limits. The start of X-Forwarded-For is whatever the visitor
    sent, so it is read from the right like network_ip(): a fake header can't dodge limits.
    """
    return network_ip(request)


def network_ip(request):
    """
    Public address the request really came from, for checking the cafe Wi-Fi.
    A phone can put anything at the start of X-Forwarded-For, so read it from
    the right and take the first public address our proxies recorded.
    """
    hops = [h.strip() for h in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            if ipaddress.ip_address(hop).is_global:
                return hop
        except ValueError:
            continue
    return request.META.get("REMOTE_ADDR", "")


def _public_hop(forwarded, fallback):
    hops = [h.strip() for h in (forwarded or "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            if ipaddress.ip_address(hop).is_global:
                return hop
        except ValueError:
            continue
    return fallback


def scope_network_ip(scope):
    """network_ip() for a websocket connection."""
    headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
    client = scope.get("client") or ("", 0)
    return _public_hop(headers.get("x-forwarded-for", ""), client[0] or "")


def ip_on_cafe_network(ip, config):
    allowed = [a.strip() for a in (getattr(config, "cafe_network_ips", "") or "").splitlines() if a.strip()]
    return not allowed or ip in allowed


def table_secret(config):
    """The secret inside every table QR code. Changing it makes all printed codes stop working."""
    import secrets
    if not config.table_qr_secret:
        # Only the first writer wins, so two first visits can't make different codes
        type(config).objects.filter(pk=config.pk, table_qr_secret="").update(table_qr_secret=secrets.token_urlsafe(18))
        config.refresh_from_db(fields=["table_qr_secret"])
    return config.table_qr_secret


def table_token(config, table_number):
    import hashlib
    import hmac
    from django.conf import settings
    key = f"{settings.SECRET_KEY}:{table_secret(config)}".encode()
    return hmac.new(key, f"table:{table_number}".encode(), hashlib.sha256).hexdigest()[:20]


def table_token_is_valid(config, table_number, token):
    import secrets
    return bool(token) and secrets.compare_digest(str(token), table_token(config, table_number))


def on_cafe_network(request, config):
    """True when no cafe network is set yet, or the request comes from one of its addresses."""
    allowed = [ip.strip() for ip in (getattr(config, "cafe_network_ips", "") or "").splitlines() if ip.strip()]
    return not allowed or network_ip(request) in allowed


def hit_limit(key, limit, window_seconds):
    """
    Counts one attempt for key and returns True when the limit for the
    time window has been passed.
    """
    cache_key = f"ratelimit:{key}"
    if cache.add(cache_key, 1, window_seconds):
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        return False
    return count > limit


def is_limited(key, limit):
    """Checks a limit without counting a new attempt."""
    return (cache.get(f"ratelimit:{key}") or 0) >= limit


def reset_limit(key):
    cache.delete(f"ratelimit:{key}")


class StaffSessionMiddleware:
    """
    Re-checks the logged-in staff member on every request: a deleted or deactivated
    account is logged out at once, and a role change applies immediately.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        staff_id = request.session.get("staff_user_id")
        if staff_id:
            from .models import StaffUser
            staff = StaffUser.objects.filter(id=staff_id).only("id", "role", "is_active").first()
            if not staff or not staff.is_active:
                for key in [k for k in request.session.keys() if k.startswith("staff_")]:
                    del request.session[key]
            elif request.session.get("staff_role") != staff.role:
                request.session["staff_role"] = staff.role
        return self.get_response(request)


def staff_is_admin(request):
    return bool(request.session.get("staff_user_id")) and request.session.get("staff_role") == "ADMIN"
