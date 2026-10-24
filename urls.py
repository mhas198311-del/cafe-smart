from django.urls import path, include, re_path
from django.views.static import serve
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf import settings
from django.conf.urls.static import static

def serve_media(request, path):
    """Uploaded files, sandboxed so a file can never run scripts on the site."""
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Content-Security-Policy"] = "sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    return response


urlpatterns = [
    path('', include('menu.urls')),
    path('', include('accounts.urls')),
    path('', include('marketing.urls')),
]

urlpatterns += staticfiles_urlpatterns()
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Uploaded videos and images (static() only works with DEBUG on)
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve_media)]
