from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

# The monthly draw lives in accounts (accounts/draws.py, /api/staff/raffle/...). These old
# demo endpoints used the unused SharedRaffleTicket pool and now only point there.
GONE = {"status": "error", "message": "The draw moved to the dashboard (/api/staff/raffle/draw/)"}


@csrf_exempt
@require_POST
def calculate_raffle_tickets(request):
    return JsonResponse(GONE, status=410)


@csrf_exempt
@require_POST
def draw_winner(request):
    return JsonResponse(GONE, status=410)


@require_GET
def get_all_tickets(request):
    return JsonResponse(GONE, status=410)
