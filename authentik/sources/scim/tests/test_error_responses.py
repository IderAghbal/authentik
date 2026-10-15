"""Test that every error under a SCIM source is a SCIM error"""

from json import dumps
from unittest.mock import patch

from rest_framework.test import APITestCase

from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE
from authentik.sources.scim.views.v2.users import UsersView

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class TestSCIMErrorResponses(APITestCase):
    """No error renders the HTML error page"""

    def setUp(self) -> None:
        self.source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.base = f"/source/scim/{self.source.slug}/v2"
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.source.token.key}"}

    def assertSCIMError(self, response, status):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response["Content-Type"].split(";")[0], SCIM_CONTENT_TYPE)
        self.assertIn(ERROR_SCHEMA, response.json().get("schemas", []))
        self.assertNotIn("<html", response.content.decode().lower())

    def test_unknown_path(self):
        """An unknown path under the source answers 404"""
        self.assertSCIMError(self.client.get(f"{self.base}/Unknown", **self.auth), 404)
        user = self.client.post(
            f"{self.base}/Users",
            data=dumps({"userName": generate_id(), "externalId": generate_id()}),
            content_type=SCIM_CONTENT_TYPE,
            **self.auth,
        ).json()
        self.assertSCIMError(
            self.client.get(f"{self.base}/Users/{user['id']}/more", **self.auth), 404
        )

    def test_search_by_post(self):
        """A search by POST answers 501"""
        response = self.client.post(
            f"{self.base}/.search", data="{}", content_type=SCIM_CONTENT_TYPE, **self.auth
        )
        self.assertSCIMError(response, 501)

    def test_unparsable_body(self):
        """A body that does not parse answers 400"""
        response = self.client.post(
            f"{self.base}/Users", data="{not json", content_type=SCIM_CONTENT_TYPE, **self.auth
        )
        self.assertSCIMError(response, 400)

    def test_missing_token(self):
        """A request without a token answers a SCIM error"""
        response = self.client.get(f"{self.base}/Users")
        self.assertIn(response.status_code, (401, 403))
        self.assertSCIMError(response, response.status_code)

    def test_unhandled_exception(self):
        """An unhandled exception answers 500"""
        with patch.object(UsersView, "get", side_effect=RuntimeError("unexpected")):
            self.assertSCIMError(self.client.get(f"{self.base}/Users", **self.auth), 500)
