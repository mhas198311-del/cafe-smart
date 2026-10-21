import random
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from accounts.models import CustomerProfile
from cafe_smart.integrations import WhatsAppClient
from .models import SharedRaffleTicket

@csrf_exempt
@require_POST
def calculate_raffle_tickets(request):
    """
    Old demo calculator that gave mock receipts to random customers. Raffle tickets
    now come from real Loyverse receipts through the Loyverse webhook.
    """
    return JsonResponse({"status": "error", "message": "Raffle tickets are generated automatically from Loyverse receipts"}, status=410)

@csrf_exempt
@require_POST
def draw_winner(request):
    """
    Selects a random ticket code, updates draw status,
    and fires an automated WhatsApp notification to the winner.
    """
    if not request.session.get("staff_user_id") or request.session.get("staff_role") != "ADMIN":
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    tickets = list(SharedRaffleTicket.objects.all())
    if not tickets:
        return JsonResponse({"status": "error", "message": "No tickets available in the pool. Run raffle calculation first."}, status=400)

    # Choose winner
    winner_ticket = random.choice(tickets)
    customer = winner_ticket.customer

    # Trigger WhatsApp Template Winner Message
    WhatsAppClient.send_winner_notification(
        phone_number=customer.phone_number,
        full_name=customer.full_name,
        ticket_code=winner_ticket.ticket_code,
        prize_name="Premium Coffee Box & 100 loyalty points"
    )

    return JsonResponse({
        "status": "success",
        "ticket_code": winner_ticket.ticket_code,
        "receipt_number": winner_ticket.loyverse_receipt_number,
        "winner_name": customer.full_name,
        "phone_number": customer.phone_number
    })

@require_GET
def get_all_tickets(request):
    """
    Returns a list of all ticket codes currently in the pool.
    Used for the staff dashboard shuffling visual animation.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    tickets = SharedRaffleTicket.objects.all().values('ticket_code', 'customer__full_name')
    return JsonResponse({
        "status": "success",
        "tickets": list(tickets)
    })
