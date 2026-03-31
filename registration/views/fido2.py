import base64
import json
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django_fido.models import Authenticator
from django_fido.views import Fido2Encoder
from fido2.server import Fido2Server
from fido2.webauthn import (
    AuthenticatorData,
    CollectedClientData,
    PublicKeyCredentialRpEntity,
)

from registration.fido2_constants import (
    FIDO2_SESSION_KEY,
    FIDO2_STATE_SESSION_KEY,
)

logger = logging.getLogger(__name__)


def _get_fido2_server(request):
    from django.conf import settings

    rp_id = (
        getattr(settings, "DJANGO_FIDO_RP_ID", None) or request.get_host().split(":")[0]
    )
    rp_name = getattr(settings, "DJANGO_FIDO_RP_NAME", rp_id)
    rp = PublicKeyCredentialRpEntity(name=rp_name, id=rp_id)
    return Fido2Server(rp)


def _get_credentials(user):
    return [a.credential for a in Authenticator.objects.filter(user=user)]


@method_decorator(staff_member_required, name="dispatch")
class Fido2VerifyRequestView(View):
    """AJAX endpoint: generates a FIDO2 authentication challenge."""

    def get(self, request):
        credentials = _get_credentials(request.user)
        if not credentials:
            return JsonResponse({"error": "No authenticators registered"}, status=400)

        server = _get_fido2_server(request)
        options, state = server.authenticate_begin(credentials)

        request.session[FIDO2_STATE_SESSION_KEY] = state

        return JsonResponse(dict(options), encoder=Fido2Encoder)


@method_decorator(staff_member_required, name="dispatch")
class Fido2VerifyView(View):
    """
    FIDO2 verification page (step 2 of admin login).

    GET:  renders the verification template with WebAuthn JS
    POST: validates the FIDO2 assertion and sets the session flag
    """

    def get(self, request):
        if request.session.get(FIDO2_SESSION_KEY):
            return redirect(reverse("admin:index"))

        return render(
            request,
            "admin/fido2_verify.html",
            {
                "title": "Security Key Verification",
                "request_url": reverse("fido2_admin:verify_request"),
                "verify_url": reverse("fido2_admin:verify"),
                "site_header": "APIS Backoffice",
            },
        )

    def post(self, request):
        state = request.session.pop(FIDO2_STATE_SESSION_KEY, None)
        if state is None:
            return HttpResponseBadRequest(
                "No FIDO2 challenge in session. Please try again."
            )

        try:
            data = json.loads(request.body)

            credential_id = base64.b64decode(data["credential_id"])
            client_data = CollectedClientData(base64.b64decode(data["client_data"]))
            authenticator_data = AuthenticatorData(
                base64.b64decode(data["authenticator_data"])
            )
            signature = base64.b64decode(data["signature"])

            credentials = _get_credentials(request.user)
            server = _get_fido2_server(request)

            credential = server.authenticate_complete(
                state,
                credentials,
                credential_id,
                client_data,
                authenticator_data,
                signature,
            )

            # Update counter for replay protection
            device = Authenticator.objects.get(
                user=request.user,
                credential_id_data=base64.b64encode(credential.credential_id).decode(
                    "utf-8"
                ),
            )
            device.counter = authenticator_data.counter
            device.save(update_fields=["counter"])

            request.session[FIDO2_SESSION_KEY] = True

            logger.info(
                "FIDO2 verification successful for user %s", request.user.username
            )
            return JsonResponse({"status": "ok", "redirect": reverse("admin:index")})

        except Exception:
            logger.warning(
                "FIDO2 verification failed for user %s",
                request.user.username,
                exc_info=True,
            )
            return JsonResponse(
                {
                    "status": "error",
                    "message": "Verification failed. Please try again.",
                },
                status=400,
            )
