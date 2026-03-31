import logging

from django.shortcuts import redirect
from django.urls import reverse

from registration.fido2_constants import FIDO2_SESSION_KEY

logger = logging.getLogger(__name__)


class Fido2VerificationMiddleware:
    """
    Enforces FIDO2 second-factor verification for admin users who have
    registered FIDO2 authenticators.

    After password login, if the user has FIDO2 keys but hasn't verified
    this session, they are redirected to the FIDO2 verification page.
    Users without keys pass through (gradual rollout).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/admin/"):
            return self.get_response(request)

        # Allow through: FIDO2 URLs, login, logout, static, jsi18n
        if request.path.startswith("/admin/fido2/"):
            return self.get_response(request)
        if request.path in ("/admin/login/", "/admin/logout/"):
            return self.get_response(request)
        if request.path.startswith("/static/") or request.path.startswith(
            "/admin/jsi18n/"
        ):
            return self.get_response(request)

        if not hasattr(request, "user") or not request.user.is_authenticated:
            return self.get_response(request)

        # Check if user has FIDO2 keys registered
        from django_fido.models import Authenticator

        if not Authenticator.objects.filter(user=request.user).exists():
            return self.get_response(request)

        # Check if FIDO2 verification is complete for this session
        if request.session.get(FIDO2_SESSION_KEY):
            return self.get_response(request)

        logger.info(
            "FIDO2 verification required for user %s, redirecting",
            request.user.username,
        )
        return redirect(reverse("fido2_admin:verify"))
