"""Test that every write of a SCIM group's members applies"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import Group
from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE

PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


class TestSCIMGroupMemberWrites(APITestCase):
    """Each form a directory writes a group's members in changes them"""

    def setUp(self) -> None:
        self.source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.users = [
            self.call(
                "post", "users", {"userName": generate_id(), "externalId": generate_id()}
            ).json()["id"]
            for _ in range(3)
        ]
        self.name = generate_id()
        self.group = self.call(
            "post",
            "groups",
            {
                "displayName": self.name,
                "externalId": generate_id(),
                "members": [{"value": self.users[0]}, {"value": self.users[1]}],
            },
        ).json()["id"]

    def call(self, method: str, kind: str, data: dict, object_id: str | None = None):
        kwargs = {"source_slug": self.source.slug}
        if object_id:
            kwargs["group_id"] = object_id
        response = getattr(self.client, method)(
            reverse(f"authentik_sources_scim:v2-{kind}", kwargs=kwargs),
            data=dumps(data),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {self.source.token.key}",
        )
        self.assertLess(response.status_code, 300, response.content)
        return response

    def patch(self, *operations: dict):
        self.call(
            "patch",
            "groups",
            {"schemas": [PATCH_OP], "Operations": list(operations)},
            object_id=self.group,
        )

    def members(self) -> list[str]:
        return sorted(str(user.uuid) for user in Group.objects.get(pk=self.group).users.all())

    def named(self, *indexes: int) -> list[str]:
        return sorted(self.users[index] for index in indexes)

    def test_op_case(self):
        """An operation applies whatever the case of its op, as Entra writes
        Add and Remove"""
        for add, remove in (("Add", "Remove"), ("add", "remove"), ("ADD", "REMOVE")):
            with self.subTest(op=add):
                self.patch({"op": add, "path": "members", "value": [{"value": self.users[2]}]})
                self.assertEqual(self.members(), self.named(0, 1, 2))
                self.patch({"op": remove, "path": "members", "value": [{"value": self.users[2]}]})
                self.assertEqual(self.members(), self.named(0, 1))

    def test_replace(self):
        """A replace of members makes the users named the members"""
        self.patch({"op": "replace", "path": "members", "value": [{"value": self.users[2]}]})
        self.assertEqual(self.members(), self.named(2))
        self.patch({"op": "Replace", "path": "members", "value": []})
        self.assertEqual(self.members(), [])

    def test_pathless(self):
        """A pathless add or replace naming members changes them"""
        self.patch({"op": "add", "value": {"members": [{"value": self.users[2]}]}})
        self.assertEqual(self.members(), self.named(0, 1, 2))
        self.patch({"op": "replace", "value": {"members": [{"value": self.users[1]}]}})
        self.assertEqual(self.members(), self.named(1))

    def test_remove_every_member(self):
        """A remove of members naming none removes every member"""
        self.patch({"op": "remove", "path": "members"})
        self.assertEqual(self.members(), [])

    def test_put_without_members(self):
        """A PUT naming no member leaves the group none"""
        self.call("put", "groups", {"displayName": self.name, "members": []}, object_id=self.group)
        self.assertEqual(self.members(), [])

    def test_entry_not_a_member(self):
        """An entry that is not a member is skipped, as a PUT skips it"""
        self.patch({"op": "add", "path": "members", "value": [self.users[2]]})
        self.assertEqual(self.members(), self.named(0, 1))
        self.patch({"op": "add", "path": "members", "value": ["x", {"value": self.users[2]}]})
        self.assertEqual(self.members(), self.named(0, 1, 2))
