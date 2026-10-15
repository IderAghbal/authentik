"""Test SCIM source filters by externalId and invalid filters"""

from json import dumps

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.lib.generators import generate_id
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.base import SCIM_CONTENT_TYPE


class TestSCIMFilters(APITestCase):
    """externalId filters and invalidFilter"""

    def setUp(self) -> None:
        self.source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.source.token.key}"}

    def url(self, kind):
        return reverse(
            f"authentik_sources_scim:v2-{kind}", kwargs={"source_slug": self.source.slug}
        )

    def post(self, kind, data):
        response = self.client.post(
            self.url(kind), data=dumps(data), content_type=SCIM_CONTENT_TYPE, **self.auth
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_external_id(self):
        """Users and groups filter by externalId"""
        user = self.post("users", {"userName": "alice", "externalId": generate_id()})
        self.post("users", {"userName": "bob", "externalId": generate_id()})
        group_id = generate_id()
        self.post("groups", {"displayName": generate_id(), "externalId": group_id})
        response = self.client.get(
            self.url("users"), {"filter": f'externalId eq "{user["externalId"]}"'}, **self.auth
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([r["id"] for r in response.json()["Resources"]], [user["id"]])
        response = self.client.get(
            self.url("groups"), {"filter": f'externalId eq "{group_id}"'}, **self.auth
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([r["externalId"] for r in response.json()["Resources"]], [group_id])

    def test_invalid_filter(self):
        """A filter that does not parse or names an attribute the endpoint
        does not filter on answers 400 invalidFilter"""
        self.post("users", {"userName": "alice", "externalId": generate_id()})
        for query in [
            'userName zz "x"',
            'nickName eq "x"',
            'userName eq "alice" and nickName eq "x"',
            "(((",
        ]:
            with self.subTest(query=query):
                response = self.client.get(self.url("users"), {"filter": query}, **self.auth)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json().get("scimType"), "invalidFilter")
