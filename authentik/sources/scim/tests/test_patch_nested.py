"""Test that PATCH changes to nested and multi-valued attributes are kept"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.models import User
from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE

PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


class TestSCIMPatchNested(APITestCase):
    """A PATCH to a nested value changes the user"""

    def setUp(self) -> None:
        self.source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        response = self.client.post(
            reverse("authentik_sources_scim:v2-users", kwargs={"source_slug": self.source.slug}),
            data=dumps(
                {
                    "userName": generate_id(),
                    "externalId": generate_id(),
                    "name": {"formatted": "Old Name"},
                    "emails": [{"type": "work", "value": "old@example.com", "primary": True}],
                }
            ),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {self.source.token.key}",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.user_id = response.json()["id"]

    def patch(self, *operations):
        url = reverse(
            "authentik_sources_scim:v2-users",
            kwargs={"source_slug": self.source.slug, "user_id": self.user_id},
        )
        response = self.client.patch(
            url,
            data=dumps({"schemas": [PATCH_OP], "Operations": list(operations)}),
            content_type=SCIM_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {self.source.token.key}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.source.token.key}").json()

    def test_nested_value(self):
        """name.formatted reaches the user"""
        read = self.patch({"op": "replace", "path": "name.formatted", "value": "New Name"})
        self.assertEqual(read["name"]["formatted"], "New Name")
        self.assertEqual(User.objects.get(uuid=self.user_id).name, "New Name")

    def test_filtered_value(self):
        """emails[type eq "work"].value reaches the user"""
        read = self.patch(
            {"op": "replace", "path": 'emails[type eq "work"].value', "value": "new@example.com"}
        )
        self.assertEqual([email["value"] for email in read["emails"]], ["new@example.com"])
        self.assertEqual(User.objects.get(uuid=self.user_id).email, "new@example.com")

    def test_whole_multi_valued(self):
        """Replacing all emails reaches the user"""
        self.patch(
            {
                "op": "replace",
                "path": "emails",
                "value": [{"type": "work", "value": "whole@example.com", "primary": True}],
            }
        )
        self.assertEqual(User.objects.get(uuid=self.user_id).email, "whole@example.com")
