"""Template-level smoke tests.

Two layers:

1. ``TestTemplates.test_validate_templates`` — Django's built-in
   ``validate_templates`` command. Catches template-tag syntax errors at
   collection time before any view runs.
2. ``TestTemplateDirectRendering`` — load a handful of templates by name via
   ``django.template.loader.get_template`` and render them with minimal
   context. This catches typos in tags/filters that only surface at render
   time and aren't reachable via ``validate_templates`` (which only parses).

The broader rendering-with-context coverage lives in
``test_views_rendering.py`` — this file is the low-level safety net.
"""

import unittest

from django.core.management import call_command
from django.template.loader import get_template
from django.test import TestCase


class TestTemplates(unittest.TestCase):
    def test_validate_templates(self):
        call_command("validate_templates")
        # This throws an error if it fails to validate


class TestTemplateDirectRendering(TestCase):
    """Load each non-context-heavy template and render it with a minimal
    context. Anything that depends on a model instance is better covered via
    the view-level tests in :mod:`test_views_rendering`.

    The goal here: catch typos in custom tags / filters / block names that
    don't parse cleanly during ``validate_templates`` but blow up on first
    render.
    """

    SIMPLE_TEMPLATES = [
        # Simple pages with near-empty context requirements
        "registration/docs/no-event.html",
    ]

    def test_simple_templates_load(self):
        """Every template in SIMPLE_TEMPLATES must resolve."""
        for name in self.SIMPLE_TEMPLATES:
            template = get_template(name)
            self.assertIsNotNone(template)

    def test_base_template_resolves(self):
        """Project-level base template must load."""
        template = get_template("base.html")
        self.assertIsNotNone(template)
