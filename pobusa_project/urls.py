# pobusa_project/urls.py — v1.0.0
# Full replacement file — copy this over pobusa_project\urls.py entirely.

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/pobusa/", include("pobusa.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
