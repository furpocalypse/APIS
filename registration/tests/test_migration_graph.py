"""S32: migration-graph integrity (locking test, no DB).

Proves model state == migration state for the ``registration`` app via
Django's own autodetector (what ``makemigrations`` runs internally) —
without a database — and that the change is **env-independent** (the
APIS_DEFAULT_EMAIL artifact is the bug S32 closes). Also asserts a single
linear leaf.
"""

from django.apps import apps
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase


def _registration_changes():
    """Return the migration operations the autodetector would generate
    for the ``registration`` app (empty == graph is in sync)."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
        NonInteractiveMigrationQuestioner(specified_apps=set()),
    )
    changes = autodetector.changes(graph=loader.graph)
    return changes.get("registration", [])


class TestMigrationGraphIntegrity(SimpleTestCase):
    def test_no_unmade_registration_migrations(self):
        self.assertEqual(
            _registration_changes(),
            [],
            "registration model state diverges from its migrations — "
            "run makemigrations (S32 graph must stay clean).",
        )

    def test_env_independence_is_structural_not_value_based(self):
        # S32 core (peer-review Radical-Doubt #6): the previous test
        # mock.patch.dict'd APIS_DEFAULT_EMAIL at test time, but
        # settings.APIS_DEFAULT_EMAIL is resolved at settings import —
        # long before the patch — so it proved nothing (vacuous). The
        # REAL guarantee is structural: the Event email fields default to
        # the *callable* registration.models.default_registration_email
        # (a function object, NOT a resolved string), and migration 0124
        # records that same callable. Django serializes a callable default
        # by import path, not by return value, so model-state == migration
        # -state for ANY APIS_DEFAULT_EMAIL. Assert that structure
        # directly — this fails if someone reverts to default=<string>.
        import importlib

        from django.apps import apps as django_apps

        from registration.models import default_registration_email

        self.assertTrue(callable(default_registration_email))
        event = django_apps.get_model("registration", "Event")
        for fname in ("registrationEmail", "staffEmail", "dealerEmail"):
            default = event._meta.get_field(fname).default
            self.assertIs(
                default,
                default_registration_email,
                f"Event.{fname}.default must be the callable (env-independent "
                "migration serialization), not a resolved string",
            )
        m0124 = importlib.import_module("registration.migrations.0124_event_email_callable_default")
        for op in m0124.Migration.operations:
            self.assertIs(
                op.field.default,
                default_registration_email,
                "migration 0124 must record the callable by reference, "
                "so makemigrations is stable across APIS_DEFAULT_EMAIL",
            )

    def test_single_linear_leaf_for_registration(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        leaves = [name for (app, name) in loader.graph.leaf_nodes() if app == "registration"]
        self.assertEqual(len(leaves), 1, f"registration migrations must have one leaf: {leaves}")
