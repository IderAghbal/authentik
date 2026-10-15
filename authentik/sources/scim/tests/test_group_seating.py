"""Test a SCIM source seating its users and nesting its groups in a group"""

from json import dumps

from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import Group, User
from authentik.core.tests.utils import create_test_admin_user
from authentik.lib.generators import generate_id
from authentik.rbac.models import Role
from authentik.sources.scim.models import SCIMSource, SCIMSourceGroup, SCIMSourceUser
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE


class SourceTestCase(APITestCase):
    """Provisions through a source's SCIM endpoints"""

    def post(self, source: SCIMSource, kind: str) -> str:
        data = {"externalId": generate_id()}
        data["userName" if kind == "users" else "displayName"] = generate_id()
        response = self.client.post(
            reverse(f"authentik_sources_scim:v2-{kind}", kwargs={"source_slug": source.slug}),
            data=dumps(data),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {source.token.key}",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]


class TestSCIMSourceGroupSeating(SourceTestCase):
    """A source with a group seats what it provisions there"""

    def setUp(self) -> None:
        self.group = Group.objects.create(name=generate_id())
        self.source = SCIMSource.objects.create(
            name=generate_id(), slug=generate_id(), group=self.group
        )

    def test_user_seated_until_its_connection_goes(self):
        """A provisioned user is seated, and unseated when its connection goes"""
        user = User.objects.get(uuid=self.post(self.source, "users"))
        self.assertTrue(self.group.users.filter(pk=user.pk).exists())
        SCIMSourceUser.objects.get(source=self.source, user=user).delete()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertFalse(self.group.users.filter(pk=user.pk).exists())

    def test_group_nested(self):
        """A group the source creates nests under the source's group"""
        group = Group.objects.get(pk=self.post(self.source, "groups"))
        self.assertEqual(list(group.parents.all()), [self.group])

    def test_without_group(self):
        """A source without a group seats nobody and nests nothing"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        user = User.objects.get(uuid=self.post(source, "users"))
        self.assertFalse(Group.objects.filter(users=user).exists())
        group = Group.objects.get(pk=self.post(source, "groups"))
        self.assertFalse(group.parents.exists())

    def test_group_set_through_the_api(self):
        """The source's group is written through the source API"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.client.force_login(create_test_admin_user())
        response = self.client.patch(
            reverse("authentik_api:scimsource-detail", kwargs={"slug": source.slug}),
            {"group": str(self.group.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        source.refresh_from_db()
        self.assertEqual(source.group, self.group)


class TestSCIMSourceSeatingGuard(SourceTestCase):
    """A source never seats users where they would gain privileges"""

    def setUp(self) -> None:
        self.admin = create_test_admin_user()
        self.superuser_group = Group.objects.create(name=generate_id(), is_superuser=True)
        self.role_group = Group.objects.create(name=generate_id())
        self.role_group.roles.add(Role.objects.create(name=generate_id()))
        self.below_superuser = Group.objects.create(name=generate_id())
        self.below_superuser.parents.add(self.superuser_group)
        self.below_role = Group.objects.create(name=generate_id())
        self.below_role.parents.add(self.role_group)
        self.refused = {
            "superuser group": self.superuser_group,
            "group holding a role": self.role_group,
            "group below a superuser group": self.below_superuser,
            "group below a group holding a role": self.below_role,
        }

    def test_api_refuses(self):
        """The source API refuses each privileged group"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.client.force_login(self.admin)
        for name, group in self.refused.items():
            with self.subTest(group=name):
                response = self.client.patch(
                    reverse("authentik_api:scimsource-detail", kwargs={"slug": source.slug}),
                    {"group": str(group.pk)},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                source.refresh_from_db()
                self.assertIsNone(source.group)

    def test_model_refuses(self):
        """Validating a source refuses each privileged group"""
        for name, group in self.refused.items():
            with self.subTest(group=name):
                source = SCIMSource(name=generate_id(), slug=generate_id(), group=group)
                with self.assertRaises(ValidationError):
                    source.full_clean()

    def test_sync_refuses_a_group_made_privileged(self):
        """A group that became privileged after the source named it seats
        nobody and nests nothing: the write fails, and nothing is created"""
        for name in self.refused:
            with self.subTest(group=name):
                group = Group.objects.create(name=generate_id())
                source = SCIMSource.objects.create(
                    name=generate_id(), slug=generate_id(), group=group
                )
                if name == "superuser group":
                    group.is_superuser = True
                    group.save()
                elif name == "group holding a role":
                    group.roles.add(Role.objects.create(name=generate_id()))
                elif name == "group below a superuser group":
                    group.parents.add(self.superuser_group)
                else:
                    group.parents.add(self.role_group)
                for kind, field in (("users", "userName"), ("groups", "displayName")):
                    value = generate_id()
                    response = self.client.post(
                        reverse(
                            f"authentik_sources_scim:v2-{kind}",
                            kwargs={"source_slug": source.slug},
                        ),
                        data=dumps({field: value, "externalId": generate_id()}),
                        content_type=SCIM_CONTENT_TYPE,
                        HTTP_AUTHORIZATION=f"Bearer {source.token.key}",
                    )
                    self.assertEqual(response.status_code, 403, response.content)
                self.assertFalse(group.users.exists())
                self.assertFalse(group.children.exists())

    def test_patch_refuses_a_group_made_privileged(self):
        """A members PATCH under a group that became privileged adds nobody,
        also when the group's stored attributes already list the member, as
        they do after a member was removed outside the source"""
        group = Group.objects.create(name=generate_id())
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id(), group=group)
        user = User.objects.get(uuid=self.post(source, "users"))
        child = self.post(source, "groups")
        group.is_superuser = True
        group.save()
        connection = SCIMSourceGroup.objects.get(source=source, group__pk=child)
        connection.attributes["members"] = [{"value": str(user.uuid)}]
        connection.save()
        response = self.client.patch(
            reverse(
                "authentik_sources_scim:v2-groups",
                kwargs={"source_slug": source.slug, "group_id": child},
            ),
            data=dumps(
                {
                    "Operations": [
                        {"op": "Add", "path": "members", "value": [{"value": str(user.uuid)}]}
                    ]
                }
            ),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {source.token.key}",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(Group.objects.get(pk=child).users.filter(pk=user.pk).exists())
