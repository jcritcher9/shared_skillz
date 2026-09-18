"""Top-level URL configuration for the EasyImports web app."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("importer.urls")),
]

handler404 = "easyimports_web.views.closed_404"
handler500 = "easyimports_web.views.closed_500"
