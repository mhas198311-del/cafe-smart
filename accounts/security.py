from django.core.cache import cache


def client_ip(request):
    """Visitor IP. Render puts the real address first in X-Forwarded-For."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


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
