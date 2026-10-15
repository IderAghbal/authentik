"""Test that a SCIM source seats only the users it created in its groups"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import Group, User
from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE

PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


class TestSCIMMemberScoping(APITestCase):
    """Members another source created are not seated"""

    def setUp(self) -> None:
        self.source_a = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.source_b = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.user_a = self.create(
            self.source_a, "users", {"userName": generate_id(), "externalId": generate_id()}
        )
        self.user_b = self.create(
            self.source_b, "users", {"userName": generate_id(), "externalId": generate_id()}
        )
        self.group_a = self.create(
            self.source_a,
            "groups",
            {
                "displayName": generate_id(),
                "externalId": generate_id(),
                "members": [{"value": self.user_a}],
            },
        )

    def call(self, source, method, kind, data, object_id=None):
        kwargs = {"source_slug": source.slug}
        if object_id:
            kwargs["group_id"] = object_id
        return getattr(self.client, method)(
            reverse(f"authentik_sources_scim:v2-{kind}", kwargs=kwargs),
            data=dumps(data),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {source.token.key}",
        )

    def create(self, source, kind, data):
        response = self.call(source, "post", kind, data)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def members(self, group_id):
        return sorted(str(user.uuid) for user in Group.objects.get(pk=group_id).users.all())

    def test_create_seats_only_own_users(self):
        """A group created with another source's user seats only its own"""
        group_b = self.create(
            self.source_b,
            "groups",
            {
                "displayName": generate_id(),
                "externalId": generate_id(),
                "members": [{"value": self.user_a}, {"value": self.user_b}],
            },
        )
        self.assertEqual(self.members(group_b), [self.user_b])
        self.assertEqual(self.members(self.group_a), [self.user_a])

    def test_patch_seats_only_own_users(self):
        """A PATCH adding another source's user seats nobody"""
        group_b = self.create(
            self.source_b, "groups", {"displayName": generate_id(), "externalId": generate_id()}
        )
        response = self.call(
            self.source_b,
            "patch",
            "groups",
            {
                "schemas": [PATCH_OP],
                "Operations": [{"op": "add", "path": "members", "value": [{"value": self.user_a}]}],
            },
            object_id=group_b,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.members(group_b), [])
        self.assertEqual(self.members(self.group_a), [self.user_a])

    def test_no_write_seats_a_foreign_user(self):
        """No write of members seats a user the source did not create,
        whatever its path or op: another source's user, or one no source
        created"""
        name = generate_id()
        group_b = self.create(
            self.source_b, "groups", {"displayName": name, "externalId": generate_id()}
        )
        stock = str(User.objects.create(username=generate_id()).uuid)
        for foreign in (self.user_a, stock):
            members = [{"value": foreign}]
            writes = {
                "POST": ("post", None, {"displayName": generate_id(), "members": members}),
                "PUT": ("put", group_b, {"displayName": name, "members": members}),
                "add": ("patch", group_b, {"op": "add", "path": "members", "value": members}),
                "add one": (
                    "patch",
                    group_b,
                    {"op": "Add", "path": "members", "value": members[0]},
                ),
                "replace": (
                    "patch",
                    group_b,
                    {"op": "replace", "path": "members", "value": members},
                ),
                "pathless add": ("patch", group_b, {"op": "add", "value": {"members": members}}),
                "pathless replace": (
                    "patch",
                    group_b,
                    {"op": "replace", "value": {"members": members}},
                ),
                "remove": ("patch", group_b, {"op": "Remove", "path": "members", "value": members}),
                "filtered remove": (
                    "patch",
                    group_b,
                    {"op": "remove", "path": f'members[value eq "{foreign}"]'},
                ),
            }
            for write, (method, object_id, data) in writes.items():
                with self.subTest(
                    member="another source's" if foreign == self.user_a else "stock", write=write
                ):
                    body = (
                        {"schemas": [PATCH_OP], "Operations": [data]} if method == "patch" else data
                    )
                    response = self.call(self.source_b, method, "groups", body, object_id=object_id)
                    self.assertLess(response.status_code, 500, response.content)
                    self.assertFalse(
                        Group.objects.filter(
                            users__uuid=foreign, scimsourcegroup__source=self.source_b
                        ).exists()
                    )
