from django.urls import path

from registration.views.fido2 import Fido2VerifyRequestView, Fido2VerifyView

app_name = "fido2_admin"

urlpatterns = [
    path("", Fido2VerifyView.as_view(), name="verify"),
    path("request/", Fido2VerifyRequestView.as_view(), name="verify_request"),
]
