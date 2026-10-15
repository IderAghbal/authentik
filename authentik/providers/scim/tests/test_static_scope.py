"""Test SCIM provider user scoping with static bindings"""

import sys
from random import Random
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

NEGATED = 0.3
ENABLED = 0.85


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
        created = User.objects.bulk_create(
            User(username=generate_id(), name="test") for _ in range(count)
        )
        if group:
            through = User.groups.through
            through.objects.bulk_create(
                through(user_id=user.pk, group_id=group.pk) for user in created
            )
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
                    PolicyEngineMode.MODE_ALL
                    if name.endswith(", all")
                    else PolicyEngineMode.MODE_ANY
                )
                self.app.save()
                for order, binding in enumerate(bindings):
                    PolicyBinding.objects.create(target=self.app, order=order, **binding)
                self.assertEqual(
                    set(self.provider.get_object_qs(User).values_list("pk", flat=True)),
                    self.engine(),
                )

    def test_same_scope_as_engine_over_generated_bindings(self):
        """Over generated binding sets the scope is the engine's, user by user:
        empty bindings, bindings naming a group and a user, negations,
        disabled bindings, both modes, and a hierarchy with a deep chain and a
        group of two parents, users seated anywhere in it"""
        rng = Random(305)
        groups = [Group.objects.create(name=generate_id()) for _ in range(6)]
        groups[1].parents.add(groups[0])
        groups[2].parents.add(groups[1])
        groups[3].parents.add(groups[2])
        groups[4].parents.add(groups[0], groups[5])
        users = self.users(12)
        for user in users:
            for group in rng.sample(groups, rng.randint(0, 2)):
                group.users.add(user)
        for case in range(120):
            PolicyBinding.objects.filter(target=self.app).delete()
            self.app.policy_engine_mode = rng.choice(
                [PolicyEngineMode.MODE_ANY, PolicyEngineMode.MODE_ALL]
            )
            self.app.save()
            shape = []
            for order in range(rng.randint(0, 4)):
                kind = rng.choice(["empty", "user", "group", "group and user"])
                binding = {
                    "negate": rng.random() < NEGATED,
                    "enabled": rng.random() < ENABLED,
                    "user": rng.choice(users) if "user" in kind else None,
                    "group": rng.choice(groups) if "group" in kind else None,
                }
                PolicyBinding.objects.create(target=self.app, order=order, **binding)
                shape.append((kind, binding["negate"], binding["enabled"]))
            with self.subTest(case=case, mode=self.app.policy_engine_mode, bindings=shape):
                provider = SCIMProvider.objects.get(pk=self.provider.pk)
                self.assertEqual(
                    set(provider.get_object_qs(User).values_list("pk", flat=True)), self.engine()
                )

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
            elapsed = monotonic() - started
            sys.stderr.write(
                f"\nscoping one page at {total} users: {len(queries)} queries, {elapsed:.2f} s\n"
            )
        self.assertEqual(costs[2_000], costs[20_000])
        self.assertLess(costs[20_000], 10)
