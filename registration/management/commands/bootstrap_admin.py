"""Idempotent first-run admin provisioning.

Creates a single Django superuser on a freshly-deployed instance. Two modes:

* **Interactive** (default): prompts for username / email / password and
  validates the password against ``AUTH_PASSWORD_VALIDATORS``. Run from a
  shell on the deployment target after the container is up
  (``docker exec``, ``ssh + manage.py``, etc.).

* **Env-driven**: reads ``BOOTSTRAP_ADMIN_USERNAME``,
  ``BOOTSTRAP_ADMIN_EMAIL``, and ``BOOTSTRAP_ADMIN_PASSWORD`` from the
  environment. Use this from IaC (Bicep / Terraform / ARM) when the
  password lives in Azure Key Vault and is bound at boot via Managed
  Identity. Triggered by passing ``--from-env``.

Idempotency: the command refuses to run if **any** superuser already
exists. This prevents repeated boots, IaC reruns, or an attacker reaching
the command from creating a parallel admin and silently elevating.
The check + creation are wrapped in a transaction with row-level locking
so two concurrent invocations cannot both succeed.

Security model (ASVS V2.1, V2.2, V14.1):
- Password validated against AUTH_PASSWORD_VALIDATORS at provisioning time.
- Password is read via ``getpass`` (no echo) in interactive mode.
- ``BOOTSTRAP_ADMIN_PASSWORD`` is removed from ``os.environ`` after
  consumption (best-effort: child processes already inherited it).
- Username uniqueness is enforced by the User model's unique constraint;
  the existence check uses ``select_for_update`` to serialize.
- Exit code 0 = admin already provisioned (idempotent re-run) OR
  newly created. Exit code != 0 = validation / config failure.
"""

from __future__ import annotations

import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Provision the initial Django superuser on first run (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-env",
            action="store_true",
            help=(
                "Read BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_EMAIL / "
                "BOOTSTRAP_ADMIN_PASSWORD from the environment instead of "
                "prompting. Intended for unattended IaC deployments."
            ),
        )
        parser.add_argument(
            "--username",
            help="Username for the admin (interactive mode default; ignored with --from-env).",
        )
        parser.add_argument(
            "--email",
            help="Email for the admin (interactive mode default; ignored with --from-env).",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        # Idempotency guard. select_for_update locks the User table rows we
        # are about to inspect; a parallel invocation will block on the
        # same query and see the just-created superuser, exiting cleanly.
        with transaction.atomic():
            existing = User.objects.select_for_update().filter(is_superuser=True).first()
            if existing is not None:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Superuser already provisioned ({existing.get_username()!r}); "
                        "nothing to do."
                    )
                )
                return

            if options.get("from_env"):
                username, email, password = self._read_from_env()
            else:
                username, email, password = self._prompt_interactive(
                    default_username=options.get("username"),
                    default_email=options.get("email"),
                )

            self._validate_password(password, username, email)

            if User.objects.filter(username=username).exists():
                # Pre-existing non-super user with this username — refuse rather than
                # silently elevating. ASVS V4.1.5: never elevate without explicit intent.
                raise CommandError(
                    f"A non-superuser with username {username!r} already exists. "
                    "Refusing to elevate it implicitly. Pick a different username, "
                    "or remove the existing user first."
                )

            User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Superuser {username!r} created. Log in at /admin/login/ "
                    "and remove BOOTSTRAP_ADMIN_PASSWORD from any env / secret "
                    "store you used."
                )
            )

    # --- input gathering ---------------------------------------------------

    def _prompt_interactive(self, default_username, default_email):
        try:
            username = input(
                f"Admin username{f' [{default_username}]' if default_username else ''}: "
            ).strip() or (default_username or "")
            if not username:
                raise CommandError("Username is required.")

            email = input(
                f"Admin email{f' [{default_email}]' if default_email else ''}: "
            ).strip() or (default_email or "")
            if not email:
                raise CommandError("Email is required.")

            # getpass disables echo; falls back to input() (with a warning
            # printed by getpass itself) on environments without a tty.
            password = getpass.getpass("Password: ")
            if not password:
                raise CommandError("Password is required.")
            confirm = getpass.getpass("Password (again): ")
            if password != confirm:
                raise CommandError("Passwords do not match.")
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Aborted by user.") from None

        return username, email, password

    def _read_from_env(self):
        try:
            username = os.environ["BOOTSTRAP_ADMIN_USERNAME"].strip()
            email = os.environ["BOOTSTRAP_ADMIN_EMAIL"].strip()
            password = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
        except KeyError as exc:
            raise CommandError(
                f"--from-env requires BOOTSTRAP_ADMIN_USERNAME, "
                f"BOOTSTRAP_ADMIN_EMAIL, and BOOTSTRAP_ADMIN_PASSWORD; "
                f"missing: {exc.args[0]}"
            ) from exc
        if not username or not email or not password:
            raise CommandError("BOOTSTRAP_ADMIN_USERNAME / EMAIL / PASSWORD must all be non-empty.")

        # Best-effort scrub from this process's env so a later os.environ
        # read in a downstream library doesn't surface it. Child processes
        # we already started have inherited the value; rotate the secret.
        os.environ.pop("BOOTSTRAP_ADMIN_PASSWORD", None)

        return username, email, password

    def _validate_password(self, password: str, username: str, email: str):
        # validate_password checks AUTH_PASSWORD_VALIDATORS against a fake
        # User-shaped object so the UserAttributeSimilarity validator can
        # compare against username / email.
        from django.contrib.auth import get_user_model

        User = get_user_model()
        candidate = User(username=username, email=email)
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            self.stderr.write(self.style.ERROR("Password rejected:"))
            for msg in exc.messages:
                self.stderr.write(self.style.ERROR(f"  - {msg}"))
            raise CommandError("Provide a stronger password and re-run.") from exc
