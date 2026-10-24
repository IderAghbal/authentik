"""Test the impersonation switch the configuration holds"""

from django.test import TestCase
from django_tenants.utils import get_public_schema_name

from authentik.lib.config import CONFIG
from authentik.tenants.apps import ensure_default_tenant
from authentik.tenants.models import Tenant


class TestImpersonationConfig(TestCase):
    """impersonation is the default tenant's impersonation switch at every start, not only at
    the tenant's creation"""

    def impersonation(self) -> bool:
        return Tenant.objects.get(schema_name=get_public_schema_name()).impersonation

    def set_impersonation(self, value: bool):
        Tenant.objects.filter(schema_name=get_public_schema_name()).update(impersonation=value)

    def test_configured_off(self):
        """A configured false turns impersonation off"""
        self.set_impersonation(True)
        with CONFIG.patch("impersonation", "false"):
            ensure_default_tenant()
        self.assertFalse(self.impersonation())

    def test_configured_on(self):
        """A configured true turns it on"""
        self.set_impersonation(False)
        with CONFIG.patch("impersonation", "True"):
            ensure_default_tenant()
        self.assertTrue(self.impersonation())

    def test_unconfigured(self):
        """Without one the setting stays as it is"""
        self.set_impersonation(False)
        ensure_default_tenant()
        self.assertFalse(self.impersonation())

    def test_invalid(self):
        """A value that is neither true nor false leaves the setting and the start goes on"""
        self.set_impersonation(True)
        with CONFIG.patch("impersonation", "off"):
            ensure_default_tenant()
        self.assertTrue(self.impersonation())
