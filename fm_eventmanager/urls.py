from django.conf import settings
from django.conf.urls import include
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.views.generic import RedirectView

admin.autodiscover()

urlpatterns = [
    path(
        "robots.txt",
        lambda x: HttpResponse(
            "User-Agent: *\n\nDisallow: /", content_type="text/plain"
        ),
        name="robots_file",
    ),
    path("registration/", include("registration.urls", namespace="registration")),
    path("admin/fido2/verify/", include("registration.fido2_urls")),
    path("admin/fido2/", include("django_fido.urls")),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="registration"), name="root"),
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns
