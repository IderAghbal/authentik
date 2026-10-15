"""Test removing a group member by a filtered members path"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import Group
from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE


class TestSCIMGroupMemberFilterRemove(APITestCase):
    """members[value eq "<id>"] removes that member"""

    def test_filtered_remove(self):
        """The named member leaves the group and the other stays"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        auth = {"HTTP_AUTHORIZATION": f"Bearer {source.token.key}"}

        def post(kind, data):
            response = self.client.post(
                reverse(f"authentik_sources_scim:v2-{kind}", kwargs={"source_slug": source.slug}),
                data=dumps(data),
                content_type=SCIM_CONTENT_TYPE,
                **auth,
            )
            self.assertEqual(response.status_code, 201, response.content)
            return response.json()["id"]

        first = post("users", {"userName": generate_id(), "externalId": generate_id()})
        second = post("users", {"userName": generate_id(), "externalId": generate_id()})
        group = post(
            "groups",
            {
                "displayName": generate_id(),
                "externalId": generate_id(),
                "members": [{"value": first}, {"value": second}],
            },
        )
        url = reverse(
            "authentik_sources_scim:v2-groups", kwargs={"source_slug": source.slug, "group_id": group}
        )
        response = self.client.patch(
            url,
            data=dumps(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [{"op": "remove", "path": f'members[value eq "{first}"]'}],
                }
            ),
            content_type=SCIM_CONTENT_TYPE,
            **auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([str(user.uuid) for user in Group.objects.get(pk=group).users.all()], [second])
        read = self.client.get(url, **auth).json()
        self.assertEqual([member["value"] for member in read["members"]], [second])
