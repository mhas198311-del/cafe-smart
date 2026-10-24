"""
Birthday gifts on WhatsApp, sent once a day without a separate scheduler: the first
request after 9 in the morning claims the day and sends in the background.
"""
import datetime
import logging
import threading

from django.utils import timezone

logger = logging.getLogger(__name__)

SEND_FROM_HOUR = 9


def birthday_gift(customer, config):
    from .views import get_customer_tier
    tier = get_customer_tier(customer, config)
    return {"Gold": config.birthday_msg_gold, "Silver": config.birthday_msg_silver}.get(tier, config.birthday_msg_bronze)


def customers_with_birthday(day):
    from .models import CustomerProfile
    days = [(day.month, day.day)]
    # Members born on 29 February celebrate on the 28th in other years
    if (day.month, day.day) == (2, 28):
        try:
            datetime.date(day.year, 2, 29)
        except ValueError:
            days.append((2, 29))
    found = CustomerProfile.objects.none()
    for month, dom in days:
        found = found | CustomerProfile.objects.filter(birth_date__month=month, birth_date__day=dom)
    return found.filter(marketing_opt_in=True).exclude(birthday_sent_year=day.year)


def send_birthday_messages(day=None):
    from .models import CafeConfiguration
    from cafe_smart.integrations import WhatsAppClient
    day = day or timezone.localdate()
    config = CafeConfiguration.objects.first()
    if not config or not config.birthday_messages_enabled:
        return 0
    sent = 0
    for customer in customers_with_birthday(day):
        try:
            if WhatsAppClient.send_birthday_gift(customer.phone_number, customer.full_name, birthday_gift(customer, config)):
                sent += 1
        except Exception as e:
            logger.error(f"Birthday message to {customer.id} failed: {e}")
        # Marked either way so a failing template doesn't retry all day
        customer.birthday_sent_year = day.year
        customer.save(update_fields=["birthday_sent_year"])
    return sent


def _send_in_background(day):
    from django.db import connection
    try:
        send_birthday_messages(day)
    finally:
        connection.close()


def claim_today(config_id, today):
    """True for exactly one caller per day."""
    from django.db.models import Q
    from .models import CafeConfiguration
    return CafeConfiguration.objects.filter(
        Q(birthday_last_run__isnull=True) | Q(birthday_last_run__lt=today), pk=config_id
    ).update(birthday_last_run=today) == 1


class DailyJobsMiddleware:
    CHECK_EVERY = datetime.timedelta(minutes=10)

    def __init__(self, get_response):
        self.get_response = get_response
        self.next_check = None

    def __call__(self, request):
        response = self.get_response(request)
        now = timezone.localtime()
        # Checked every few minutes (not once a day) so turning the gifts on later in the day still sends today's;
        # claim_today() makes sure each day is sent only once
        if (self.next_check is None or now >= self.next_check) and now.hour >= SEND_FROM_HOUR:
            self.next_check = now + self.CHECK_EVERY
            try:
                from .models import CafeConfiguration
                config = CafeConfiguration.objects.only("id", "birthday_messages_enabled").first()
                if config and config.birthday_messages_enabled and claim_today(config.id, now.date()):
                    threading.Thread(target=_send_in_background, args=(now.date(),), daemon=True).start()
            except Exception as e:
                logger.error(f"Daily jobs check failed: {e}")
        return response
