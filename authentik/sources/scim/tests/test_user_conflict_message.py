"""Test the SCIM user endpoint's conflict error"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE


class TestSCIMUserConflictMessage(APITestCase):
    """Creating an existing user names a user in its error"""

    def test_conflict_names_user(self):
        """The 409 for an existing user says User"""
        source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        body = dumps({"userName": generate_id(), "externalId": generate_id()})
        url = reverse("authentik_sources_scim:v2-users", kwargs={"source_slug": source.slug})
        auth = {"HTTP_AUTHORIZATION": f"Bearer {source.token.key}"}
        self.assertEqual(
            self.client.post(url, data=body, content_type=SCIM_CONTENT_TYPE, **auth).status_code,
            201,
        )
        response = self.client.post(url, data=body, content_type=SCIM_CONTENT_TYPE, **auth)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["detail"], "User with ID exists already.")
