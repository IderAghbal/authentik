"""Test that a SCIM source reaches only the users and groups it created"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import Group, User
from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource, SCIMSourceGroup, SCIMSourcePropertyMapping
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE


class TestSCIMSourceNamespaces(APITestCase):
    """Two sources naming the same user or group get one each"""

    def setUp(self) -> None:
        self.source_a = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.source_b = SCIMSource.objects.create(name=generate_id(), slug=generate_id())

    def call(self, source, method, kind, data=None, object_id=None, query=None):
        kwargs = {"source_slug": source.slug}
        if object_id:
            kwargs["user_id" if kind == "users" else "group_id"] = object_id
        url = reverse(f"authentik_sources_scim:v2-{kind}", kwargs=kwargs)
        if query is not None:
            return self.client.get(url, query, HTTP_AUTHORIZATION=f"Bearer {source.token.key}")
        return getattr(self.client, method)(
            url,
            data=dumps(data) if data is not None else None,
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {source.token.key}",
        )

    def user(self, source, username, **extra):
        response = self.call(
            source,
            "post",
            "users",
            {
                "userName": username,
                "externalId": generate_id(),
                "name": {"formatted": username},
                **extra,
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_same_username_two_users(self):
        """The same userName from two sources makes two users, and a delete
        from one source leaves the other's"""
        user_a = self.user(self.source_a, "alice")
        user_b = self.user(self.source_b, "alice", active=False)
        self.assertNotEqual(user_a["id"], user_b["id"])
        self.assertEqual(user_b["userName"], "alice")
        self.assertTrue(User.objects.get(uuid=user_a["id"]).is_active)

        response = self.call(self.source_b, "delete", "users", object_id=user_b["id"])
        self.assertEqual(response.status_code, 204)
        response = self.call(self.source_a, "get", "users", object_id=user_a["id"])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["userName"], "alice")
        self.assertTrue(User.objects.filter(uuid=user_a["id"], is_active=True).exists())

    def test_same_group_name_two_groups(self):
        """The same displayName from two sources makes two groups, and a
        delete from one source leaves the other's"""
        group_a = self.call(
            self.source_a,
            "post",
            "groups",
            {"displayName": "Engineering", "externalId": generate_id()},
        )
        group_b = self.call(
            self.source_b,
            "post",
            "groups",
            {"displayName": "Engineering", "externalId": generate_id()},
        )
        self.assertEqual((group_a.status_code, group_b.status_code), (201, 201), group_b.content)
        self.assertNotEqual(group_a.json()["id"], group_b.json()["id"])
        self.assertEqual(group_b.json()["displayName"], "Engineering")

        response = self.call(self.source_b, "delete", "groups", object_id=group_b.json()["id"])
        self.assertEqual(response.status_code, 204)
        response = self.call(self.source_a, "get", "groups", object_id=group_a.json()["id"])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["displayName"], "Engineering")

    def test_filter_by_own_username(self):
        """A filter on userName reads the names the client sent"""
        self.user(self.source_a, "alice")
        response = self.call(self.source_a, "get", "users", query={"filter": 'userName eq "alice"'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([r["userName"] for r in response.json()["Resources"]], ["alice"])

    def test_existing_user_and_group_untouched(self):
        """A source naming an existing user or group gets its own instead,
        and the existing ones keep their state and members"""
        admin, _ = User.objects.update_or_create(
            username="akadmin", defaults={"name": "Administrator", "is_active": True}
        )
        admins, _ = Group.objects.update_or_create(
            name="authentik Admins", defaults={"is_superuser": True}
        )
        admins.users.set([admin])

        created = self.user(self.source_a, "akadmin", active=False)
        self.assertNotEqual(created["id"], str(admin.uuid))
        group = self.call(
            self.source_a,
            "post",
            "groups",
            {
                "displayName": "authentik Admins",
                "externalId": generate_id(),
                "members": [{"value": created["id"]}],
            },
        )
        self.assertEqual(group.status_code, 201, group.content)
        self.assertNotEqual(group.json()["id"], str(admins.pk))

        admin.refresh_from_db()
        admins.refresh_from_db()
        self.assertEqual((admin.is_active, admin.name), (True, "Administrator"))
        self.assertTrue(admins.is_superuser)
        self.assertEqual(list(admins.users.all()), [admin])
        response = self.call(self.source_a, "delete", "users", object_id=str(admin.uuid))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=admin.pk).exists())

    def test_conflict_on_the_mapped_name(self):
        """A group its mappings name from another attribute conflicts on that
        name: none is created beside the group holding it, and none adopted"""
        self.source_a.group_property_mappings.set(
            [
                SCIMSourcePropertyMapping.objects.create(
                    name=generate_id(), expression='return {"name": data.get("team")}'
                )
            ]
        )
        user = self.user(self.source_a, generate_id())["id"]
        first = self.call(
            self.source_a,
            "post",
            "groups",
            {"displayName": generate_id(), "externalId": generate_id(), "team": "core"},
        )
        self.assertEqual(first.status_code, 201, first.content)
        outside = Group.objects.create(name=f"{self.source_a.slug}.outside")
        for team in ("core", "outside"):
            with self.subTest(team=team):
                before = set(Group.objects.values_list("pk", flat=True))
                response = self.call(
                    self.source_a,
                    "post",
                    "groups",
                    {
                        "displayName": generate_id(),
                        "externalId": generate_id(),
                        "team": team,
                        "members": [{"value": user}],
                    },
                )
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(set(Group.objects.values_list("pk", flat=True)), before)
        self.assertFalse(SCIMSourceGroup.objects.filter(group=outside).exists())
        self.assertFalse(outside.users.exists())
        self.assertFalse(Group.objects.get(pk=first.json()["id"]).users.exists())
