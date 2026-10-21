import ipaddress

from django.core.cache import cache


def client_ip(request):
    """Visitor IP. Render puts the real address first in X-Forwarded-For."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


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


def staff_is_admin(request):
    return bool(request.session.get("staff_user_id")) and request.session.get("staff_role") == "ADMIN"
