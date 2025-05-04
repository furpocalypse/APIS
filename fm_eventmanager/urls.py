from django.conf import settings
from django.conf.urls import include
from django.contrib import admin
from django.urls import re_path
from django.views.generic import RedirectView
from django.http import HttpResponse

admin.autodiscover()

urlpatterns = [
    re_path(
        r"robots.txt",
        lambda x: HttpResponse("User-Agent: *\n\nDisallow: /", content_type="text/plain"),
        name="robots_file"
    ),
    re_path(r"^registration/", include("registration.urls", namespace="registration")),
    re_path(r"^admin/", admin.site.urls),
    re_path(r"^$", RedirectView.as_view(url="registration"), name="root"),
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns = [
        re_path(r"^__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns
