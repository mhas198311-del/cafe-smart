import json
import logging
from django.conf import settings
from pywebpush import webpush, WebPushException

logger = logging.getLogger(__name__)

def send_web_push_to_staff(title, body, url="/staff/app/", tag="table-alert"):
    """
    Sends Web Push Notification to all subscribed staff devices.
    Triggered even when mobile is locked or browser is in the background.
    """
    from .models import StaffPushSubscription
    
    subscriptions = list(StaffPushSubscription.objects.all())
    if not subscriptions:
        return 0

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
    })

    vapid_claims = {
        "sub": getattr(settings, "VAPID_ADMIN_EMAIL", "mailto:admin@whitebirdcafe.com")
    }

    success_count = 0
    to_delete_ids = []

    for sub in subscriptions:
        sub_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth
            }
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=getattr(settings, "VAPID_PRIVATE_KEY", ""),
                vapid_claims=vapid_claims
            )
            success_count += 1
        except WebPushException as ex:
            logger.warning(f"Web Push failed for sub {sub.id}: {ex}")
            if ex.response and ex.response.status_code in [404, 410]:
                to_delete_ids.append(sub.id)
        except Exception as e:
            logger.error(f"Unexpected error in webpush: {e}")

    if to_delete_ids:
        StaffPushSubscription.objects.filter(id__in=to_delete_ids).delete()

    return success_count
