"""Test the event retention the configuration holds"""

from django.test import TestCase
from django_tenants.utils import get_public_schema_name

from authentik.lib.config import CONFIG
from authentik.tenants.apps import ensure_default_tenant
from authentik.tenants.models import Tenant


class TestEventRetentionConfig(TestCase):
    """events.retention is the default tenant's event retention at every start"""

    def retention(self) -> str:
        return Tenant.objects.get(schema_name=get_public_schema_name()).event_retention

    def set_retention(self, value: str):
        Tenant.objects.filter(schema_name=get_public_schema_name()).update(event_retention=value)

    def test_configured(self):
        """A configured retention replaces the setting"""
        self.set_retention("days=365")
        with CONFIG.patch("events.retention", "days=90"):
            ensure_default_tenant()
        self.assertEqual(self.retention(), "days=90")

    def test_unconfigured(self):
        """Without one the setting stays as it is"""
        self.set_retention("weeks=3")
        ensure_default_tenant()
        self.assertEqual(self.retention(), "weeks=3")

    def test_invalid(self):
        """A retention that is no duration leaves the setting and the start goes on"""
        self.set_retention("weeks=3")
        with CONFIG.patch("events.retention", "ninety days"):
            ensure_default_tenant()
        self.assertEqual(self.retention(), "weeks=3")
