"""Test that a user's password is neither stored nor returned"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource, SCIMSourceUser
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE

SECRET = "not-a-real-password-sent-by-a-client"


class TestSCIMNeverReturned(APITestCase):
    """password is returned: never (RFC 7643)"""

    def test_password(self):
        """Create, replace and PATCH neither store nor return it"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        auth = {"HTTP_AUTHORIZATION": f"Bearer {source.token.key}"}
        body = {"userName": generate_id(), "externalId": generate_id(), "password": SECRET}

        def stored(user_id):
            return SECRET in str(SCIMSourceUser.objects.get(source=source, user__uuid=user_id).attributes)

        response = self.client.post(
            reverse("authentik_sources_scim:v2-users", kwargs={"source_slug": source.slug}),
            data=dumps(body),
            content_type=SCIM_CONTENT_TYPE,
            **auth,
        )
        self.assertEqual(response.status_code, 201, response.content)
        user_id = response.json()["id"]
        url = reverse(
            "authentik_sources_scim:v2-users", kwargs={"source_slug": source.slug, "user_id": user_id}
        )
        self.assertNotIn(SECRET, response.content.decode())
        self.assertFalse(stored(user_id))
        self.assertNotIn(SECRET, self.client.get(url, **auth).content.decode())

        response = self.client.put(url, data=dumps(body), content_type=SCIM_CONTENT_TYPE, **auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(SECRET, response.content.decode())
        self.assertFalse(stored(user_id))

        response = self.client.patch(
            url,
            data=dumps(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [
                        {"op": "replace", "path": "password", "value": SECRET},
                        {"op": "replace", "path": "displayName", "value": generate_id()},
                    ],
                }
            ),
            content_type=SCIM_CONTENT_TYPE,
            **auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(SECRET, response.content.decode())
        self.assertFalse(stored(user_id))
