from accounts.models import CafeConfiguration

def cafe_config(request):
    try:
        config = CafeConfiguration.objects.first()
        if not config:
            config = CafeConfiguration.objects.create()
    except Exception:
        config = None
    return {
        'global_config': config
    }
