"""Test a SCIM provider scoped to a group's descendants and members"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from authentik.core.models import Application, Group, User
from authentik.lib.generators import generate_id
from authentik.policies.models import PolicyBinding
from authentik.providers.scim.models import SCIMProvider


class TestSCIMProviderGroupScopeRoot(TestCase):
    """A scope root syncs its descendants and its members"""

    def setUp(self) -> None:
        self.root = Group.objects.create(name=generate_id())
        self.child = Group.objects.create(name=generate_id())
        self.child.parents.add(self.root)
        self.grandchild = Group.objects.create(name=generate_id())
        self.grandchild.parents.add(self.child)
        self.other = Group.objects.create(name=generate_id())
        self.direct, self.nested, self.outside = (
            User.objects.create(username=generate_id()) for _ in range(3)
        )
        self.root.users.add(self.direct)
        self.grandchild.users.add(self.nested)
        self.other.users.add(self.outside)

    def provider(self, **kwargs) -> tuple[SCIMProvider, Application]:
        provider = SCIMProvider.objects.create(
            name=generate_id(),
            url="https://localhost",
            token=generate_id(),
            exclude_users_service_account=True,
            **kwargs,
        )
        app = Application.objects.create(name=generate_id(), slug=generate_id())
        app.backchannel_providers.add(provider)
        return provider, app

    def test_scope_root(self):
        """The root's descendants, never the root, and the members of all three"""
        provider, _ = self.provider(group_scope_root=self.root)
        self.assertEqual(set(provider.get_object_qs(User)), {self.direct, self.nested})
        self.assertEqual(set(provider.get_object_qs(Group)), {self.child, self.grandchild})

    def test_scope_root_in_sql(self):
        """The scope costs as many queries at any number of users outside it"""
        provider, _ = self.provider(group_scope_root=self.root)
        counts = []
        for _ in range(2):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(len(list(provider.get_object_qs(User))), 2)
            counts.append(len(queries))
            User.objects.bulk_create(User(username=generate_id()) for _ in range(200))
        self.assertEqual(counts[0], counts[1])

    def test_without_scope_root(self):
        """Without one, a provider shaped as the platform directory's is stock:
        its group filter's group and the members its group binding passes"""
        provider, app = self.provider()
        provider.group_filters.set([self.other])
        PolicyBinding.objects.create(target=app, group=self.other, order=0)
        self.assertEqual(set(provider.get_object_qs(User)), {self.outside})
        self.assertEqual(set(provider.get_object_qs(Group)), {self.other})
