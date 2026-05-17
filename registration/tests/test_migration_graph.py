"""S32: migration-graph integrity (locking test, no DB).

Proves model state == migration state for the ``registration`` app via
Django's own autodetector (what ``makemigrations`` runs internally) —
without a database — and that the change is **env-independent** (the
APIS_DEFAULT_EMAIL artifact is the bug S32 closes). Also asserts a single
linear leaf.
"""

import os
from unittest import mock

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

    def test_clean_regardless_of_default_email(self):
        # S32 core: a different APIS_DEFAULT_EMAIL must NOT manufacture a
        # spurious Event AlterField (callable default → env-independent).
        with mock.patch.dict(os.environ, {"APIS_DEFAULT_EMAIL": "someone-else@example.org"}):
            self.assertEqual(_registration_changes(), [])

    def test_single_linear_leaf_for_registration(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        leaves = [name for (app, name) in loader.graph.leaf_nodes() if app == "registration"]
        self.assertEqual(len(leaves), 1, f"registration migrations must have one leaf: {leaves}")
