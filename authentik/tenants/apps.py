"""authentik tenants app"""

from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import post_migrate
from django_tenants.utils import get_public_schema_name
from structlog.stdlib import get_logger

from authentik.blueprints.apps import ManagedAppConfig

LOGGER = get_logger()


def ensure_default_tenant(*args, using=DEFAULT_DB_ALIAS, **kwargs):
    """Make sure default tenant exists, and hold its impersonation switch to the configured
    impersonation when one is set"""
    from django_tenants.utils import schema_context

    from authentik.lib.config import CONFIG
    from authentik.tenants.models import Tenant

    defaults = {"name": "Default", "ready": True}
    impersonation = CONFIG.get("impersonation")
    if impersonation is not None and impersonation != "":
        if str(impersonation).lower() in ("true", "false"):
            defaults["impersonation"] = str(impersonation).lower() == "true"
        else:
            LOGGER.error("impersonation is neither true nor false, impersonation left as it is")
    with schema_context(get_public_schema_name()):
        Tenant.objects.using(using).update_or_create(
            defaults=defaults,
            schema_name=get_public_schema_name(),
        )


class AuthentikTenantsConfig(ManagedAppConfig):
    """authentik tenants app"""

    name = "authentik.tenants"
    label = "authentik_tenants"
    verbose_name = "authentik Tenants"
    default = True

    @ManagedAppConfig.reconcile_global
    def default_tenant(self):
        """Make sure default tenant exists, especially after a migration"""
        post_migrate.connect(ensure_default_tenant)
        ensure_default_tenant()
