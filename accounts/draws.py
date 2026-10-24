"""
Monthly draw: every receipt a registered member pays in a month is one entry.
Three winners (different members) are picked at random, each with the prize of their place.
"""
import datetime
import logging
import random

from django.utils import timezone

logger = logging.getLogger(__name__)

PLACE_NAMES = {1: "المركز الأول", 2: "المركز الثاني", 3: "المركز الثالث"}
MONTH_NAMES = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
               "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]


def month_start(day=None):
    day = day or timezone.localdate()
    return day.replace(day=1)


def next_month(month):
    return (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def month_name_ar(month):
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def parse_month(value):
    """'2026-10' → 2026-10-01, or the current month when empty or unreadable."""
    try:
        year, month = str(value).split("-")[:2]
        return datetime.date(int(year), int(month), 1)
    except (TypeError, ValueError):
        return month_start()


def get_draw(month):
    from .models import MonthlyDraw, CafeConfiguration
    draw, created = MonthlyDraw.objects.get_or_create(month=month)
    if created:
        # The single prize from the old settings becomes this month's first prize
        config = CafeConfiguration.objects.first()
        if config and config.raffle_prize_name and config.raffle_prize_name != "Premium Coffee Box":
            draw.prize1_name = config.raffle_prize_name
            if config.raffle_prize_image:
                draw.prize1_image = config.raffle_prize_image.url
            draw.save()
    return draw


def month_entries(month):
    from .models import RaffleTicket
    start = timezone.make_aware(datetime.datetime.combine(month, datetime.time.min))
    end = timezone.make_aware(datetime.datetime.combine(next_month(month), datetime.time.min))
    return RaffleTicket.objects.filter(created_at__gte=start, created_at__lt=end).select_related("customer")


def _ticket_json(ticket):
    if not ticket:
        return None
    return {
        "receipt": ticket.receipt_number,
        "ticket": ticket.ticket_code,
        "customer": ticket.customer.full_name,
        "at": timezone.localtime(ticket.created_at).strftime("%Y/%m/%d %H:%M"),
    }


def draw_info(draw):
    entries = month_entries(draw.month).order_by("created_at")
    winners = draw.winners.select_related("customer").order_by("place")
    return {
        "month": draw.month.strftime("%Y-%m"),
        "month_name": month_name_ar(draw.month),
        "entries": entries.count(),
        "members": entries.values("customer_id").distinct().count(),
        "first": _ticket_json(entries.first()),
        "last": _ticket_json(entries.last()),
        "prizes": [{"place": p, "place_name": PLACE_NAMES[p], "name": n, "image": i} for p, n, i in draw.prizes()],
        "drawn_at": timezone.localtime(draw.drawn_at).strftime("%Y/%m/%d %H:%M") if draw.drawn_at else "",
        "winners": [{
            "place": t.place, "place_name": PLACE_NAMES.get(t.place, ""),
            "name": t.customer.full_name, "phone": t.customer.phone_number,
            "ticket": t.ticket_code, "receipt": t.receipt_number,
        } for t in winners],
    }


def run_draw(draw):
    """Picks up to three different members from the month's entries and messages each winner."""
    from django.db import transaction
    from .models import MonthlyDraw
    from cafe_smart.integrations import WhatsAppClient
    with transaction.atomic():
        draw = MonthlyDraw.objects.select_for_update().get(pk=draw.pk)
        if draw.drawn_at:
            raise ValueError("سحب هالشهر صار قبل")
        tickets = list(month_entries(draw.month).filter(draw_status="PENDING"))
        if not tickets:
            raise ValueError("ما في مشاركات بهالشهر لسا")
        random.SystemRandom().shuffle(tickets)
        winners, seen = [], set()
        for ticket in tickets:
            if ticket.customer_id not in seen:
                seen.add(ticket.customer_id)
                winners.append(ticket)
            if len(winners) == 3:
                break
        prizes = {p: n for p, n, _ in draw.prizes()}
        for place, ticket in enumerate(winners, 1):
            ticket.draw_status = "WON"
            ticket.draw = draw
            ticket.place = place
            ticket.save(update_fields=["draw_status", "draw", "place"])
        winner_ids = [t.id for t in winners]
        month_entries(draw.month).filter(draw_status="PENDING").exclude(id__in=winner_ids).update(draw_status="LOST")
        draw.drawn_at = timezone.now()
        draw.save(update_fields=["drawn_at"])
    for ticket in winners:
        prize = prizes.get(ticket.place) or "جائزة السحب"
        try:
            WhatsAppClient.send_winner_notification(
                ticket.customer.phone_number, ticket.customer.full_name, ticket.ticket_code,
                f"{PLACE_NAMES[ticket.place]}: {prize}")
        except Exception as e:
            logger.error(f"Failed to send winner WhatsApp message: {e}")
    return draw
