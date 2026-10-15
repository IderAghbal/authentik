"""Test that a SCIM source's token authenticates only its source's endpoints"""

from django.test import RequestFactory
from rest_framework.test import APITestCase

from authentik.core.models import Token, TokenIntents, User, UserTypes
from authentik.lib.generators import generate_id
from authentik.root.middleware import ClientIPMiddleware
from authentik.sources.scim.models import SCIMSource


class TestSCIMTokenScope(APITestCase):
    """The API refuses a SCIM source's token"""

    def setUp(self) -> None:
        self.source = SCIMSource.objects.create(name=generate_id(), slug=generate_id())
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.source.token.key}"}

    def test_source_endpoints(self):
        """The token still authenticates its source's endpoints"""
        response = self.client.get(f"/source/scim/{self.source.slug}/v2/Users", **self.auth)
        self.assertEqual(response.status_code, 200, response.content)

    def test_api_refused(self):
        """No endpoint of the API authenticates the token"""
        for path in (
            "/api/v3/core/users/me/",
            "/api/v3/core/users/",
            "/api/v3/core/groups/",
            "/api/v3/core/tokens/",
            "/api/v3/admin/version/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, **self.auth)
                self.assertIn(response.status_code, (401, 403), response.content)

    def test_admin_interface_anonymous(self):
        """The admin interface reads the token as no one"""
        response = self.client.get("/if/admin/", **self.auth)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_deleted_source_token(self):
        """A deleted source's token authenticates nothing: it goes with the
        source's service account"""
        key = self.source.token.key
        self.source.delete()
        self.assertFalse(Token.objects.filter(key=key).exists())
        response = self.client.get("/api/v3/core/users/me/", HTTP_AUTHORIZATION=f"Bearer {key}")
        self.assertIn(response.status_code, (401, 403), response.content)

    def test_api_token(self):
        """An API token of a user still authenticates the API"""
        user = User.objects.create(username=generate_id())
        token = Token.objects.create(
            identifier=generate_id(), user=user, intent=TokenIntents.INTENT_API, expiring=False
        )
        response = self.client.get(
            "/api/v3/core/users/me/", HTTP_AUTHORIZATION=f"Bearer {token.key}"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def remote_ip_request(self, key: str):
        return RequestFactory().get(
            "/",
            **{
                ClientIPMiddleware.outpost_remote_ip_header: "192.0.2.7",
                ClientIPMiddleware.outpost_token_header: key,
            },
        )

    def test_outpost_remote_ip_refused(self):
        """The token cannot set the client's IP and pass as an outpost, though
        the source's account is an internal service account as an outpost's is"""
        request = self.remote_ip_request(self.source.token.key)
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "127.0.0.1")
        self.assertIsNone(ClientIPMiddleware.get_outpost_user(request))

    def test_outpost_remote_ip(self):
        """An internal service account's token of no source still sets it"""
        user = User.objects.create(username=generate_id(), type=UserTypes.INTERNAL_SERVICE_ACCOUNT)
        token = Token.objects.create(
            identifier=generate_id(), user=user, intent=TokenIntents.INTENT_API, expiring=False
        )
        request = self.remote_ip_request(token.key)
        self.assertEqual(ClientIPMiddleware.get_client_ip(request), "192.0.2.7")
        self.assertEqual(ClientIPMiddleware.get_outpost_user(request), user)
