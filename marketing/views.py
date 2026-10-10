import random
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from accounts.models import CustomerProfile
from cafe_smart.integrations import LoyverseClient, WhatsAppClient
from .models import SharedRaffleTicket

@csrf_exempt
@require_POST
def calculate_raffle_tickets(request):
    """
    Raffle Calculation Engine.
    Queries Loyverse API for receipts within date parameters and minimum transaction amount.
    Populates SharedRaffleTicket and links them to customers.
    """
    # Check if staff is logged in
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    min_amount_str = request.POST.get("min_amount", "10.00")
    try:
        min_amount = float(min_amount_str)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Invalid minimum amount"}, status=400)

    # Fetch mock receipts from integration engine
    receipts = LoyverseClient.get_receipts(min_amount=min_amount)
    
    # Get all customers to randomly assign receipts for demo purposes
    customers = list(CustomerProfile.objects.all())
    if not customers:
        return JsonResponse({
            "status": "error", 
            "message": "No registered customers found. Please register a customer loyalty profile first."
        }, status=400)

    tickets_created = 0
    skipped_receipts = 0

    for receipt in receipts:
        receipt_num = receipt["receipt_number"]
        
        # Check if ticket already issued for this receipt
        if SharedRaffleTicket.objects.filter(loyverse_receipt_number=receipt_num).exists():
            skipped_receipts += 1
            continue

        # Choose a random customer to link to (in real POS it's linked by Loyverse customer ID)
        customer = random.choice(customers)

        # Generate unique ticket code
        ticket_code = f"26C-{random.randint(100, 999)}-{random.randint(1000, 9999)}"
        while SharedRaffleTicket.objects.filter(ticket_code=ticket_code).exists():
            ticket_code = f"26C-{random.randint(100, 999)}-{random.randint(1000, 9999)}"

        SharedRaffleTicket.objects.create(
            customer=customer,
            loyverse_receipt_number=receipt_num,
            ticket_code=ticket_code
        )
        tickets_created += 1

    return JsonResponse({
        "status": "success",
        "message": f"Processed receipts. Generated {tickets_created} new tickets. Skipped {skipped_receipts} already processed receipts.",
        "tickets_created": tickets_created,
        "skipped": skipped_receipts
    })

@csrf_exempt
@require_POST
def draw_winner(request):
    """
    Selects a random ticket code, updates draw status,
    and fires an automated WhatsApp notification to the winner.
    """
    if not request.session.get("staff_user_id"):
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
    tickets = SharedRaffleTicket.objects.all().values('ticket_code', 'customer__full_name')
    return JsonResponse({
        "status": "success",
        "tickets": list(tickets)
    })
