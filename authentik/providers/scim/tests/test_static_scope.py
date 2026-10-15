"""Test SCIM provider user scoping with static bindings"""

import sys
from time import monotonic

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from authentik.blueprints.tests import apply_blueprint
from authentik.core.models import Application, Group, User, UserTypes
from authentik.lib.generators import generate_id
from authentik.policies.engine import PolicyEngine
from authentik.policies.models import PolicyBinding, PolicyEngineMode
from authentik.providers.scim.models import SCIMProvider


class TestSCIMStaticScope(TestCase):
    """Static bindings scope users as the policy engine does, in one query"""

    @apply_blueprint("system/providers-scim.yaml")
    def setUp(self) -> None:
        self.provider = SCIMProvider.objects.create(
            name=generate_id(),
            url="https://localhost",
            token=generate_id(),
            exclude_users_service_account=True,
        )
        self.app = Application.objects.create(name=generate_id(), slug=generate_id())
        self.app.backchannel_providers.add(self.provider)

    def users(self, count, group=None):
        created = User.objects.bulk_create(User(username=generate_id(), name="test") for _ in range(count))
        if group:
            through = User.groups.through
            through.objects.bulk_create(through(user_id=user.pk, group_id=group.pk) for user in created)
        return created

    def engine(self):
        base = (
            User.objects.all()
            .exclude_anonymous()
            .exclude(type__in=[UserTypes.SERVICE_ACCOUNT, UserTypes.INTERNAL_SERVICE_ACCOUNT])
        )
        return {user.pk for user in base if PolicyEngine(self.app, user, None).build().passing}

    def test_same_scope_as_engine(self):
        """Each shape of static binding scopes the users the engine passes"""
        parent, child, other = (Group.objects.create(name=generate_id()) for _ in range(3))
        child.parents.add(parent)
        self.users(2, child)
        self.users(2, other)
        alone = self.users(2)
        shapes = {
            "none": [],
            "group, member of a child group": [{"group": parent}],
            "negated group": [{"group": other, "negate": True}],
            "user": [{"user": alone[0]}],
            "negated user": [{"user": alone[0], "negate": True}],
            "two groups, any": [{"group": child}, {"group": other}],
            "two groups, all": [{"group": parent}, {"group": other}],
            "disabled binding": [{"group": child}, {"group": other, "enabled": False}],
        }
        for name, bindings in shapes.items():
            with self.subTest(shape=name):
                PolicyBinding.objects.filter(target=self.app).delete()
                self.app.policy_engine_mode = (
                    PolicyEngineMode.MODE_ALL if name.endswith(", all") else PolicyEngineMode.MODE_ANY
                )
                self.app.save()
                for order, binding in enumerate(bindings):
                    PolicyBinding.objects.create(target=self.app, order=order, **binding)
                self.assertEqual(set(self.provider.get_object_qs(User).values_list("pk", flat=True)), self.engine())

    def test_cost_flat_in_users(self):
        """Scoping a page costs the same queries at 2,000 and 20,000 users"""
        group = Group.objects.create(name=generate_id())
        PolicyBinding.objects.create(target=self.app, group=group, order=0)
        costs = {}
        for total in (2_000, 20_000):
            self.users(total // 2 - User.objects.filter(groups=group).count(), group)
            self.users(total - User.objects.count())
            started = monotonic()
            with CaptureQueriesContext(connection) as queries:
                list(self.provider.get_paginator(User).page(1).object_list)
            costs[total] = len(queries)
            sys.stderr.write(
                f"\nscoping one page at {total} users: {len(queries)} queries, {monotonic() - started:.2f} s\n"
            )
        self.assertEqual(costs[2_000], costs[20_000])
        self.assertLess(costs[20_000], 10)
