"""An OAuth source updated in part keeps what the update leaves out"""

from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.tests.utils import create_test_admin_user
from authentik.lib.generators import generate_id
from authentik.sources.oauth.models import OAuthSource


class TestPartialUpdate(APITestCase):
    """A PATCH names only what it changes: the source's type and its URLs
    stay as they are."""

    def setUp(self):
        self.source = OAuthSource.objects.create(
            name=generate_id(),
            slug=generate_id(),
            provider_type="openidconnect",
            authorization_url="https://idp.example.com/authorize",
            access_token_url="https://idp.example.com/token",
            profile_url="https://idp.example.com/userinfo",
            consumer_key=generate_id(),
            consumer_secret=generate_id(),
        )
        self.client.force_login(create_test_admin_user())
        self.url = reverse("authentik_api:oauthsource-detail", kwargs={"slug": self.source.slug})

    def test_a_patch_without_the_provider_type_updates_the_source(self):
        """A PATCH naming no provider type takes the source's own."""
        response = self.client.patch(self.url, {"name": "renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "renamed")
        self.assertEqual(self.source.provider_type, "openidconnect")

    def test_a_patch_without_the_urls_keeps_them(self):
        """A type that needs its URLs set is satisfied by the source's own."""
        response = self.client.patch(self.url, {"consumer_key": "rotated"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.consumer_key, "rotated")
        self.assertEqual(self.source.authorization_url, "https://idp.example.com/authorize")

    def test_a_source_without_its_urls_is_still_refused(self):
        """A type that needs its URLs refuses a source that has none, in part
        or whole."""
        bare = OAuthSource.objects.create(
            name=generate_id(), slug=generate_id(), provider_type="openidconnect", consumer_key=generate_id()
        )
        response = self.client.patch(
            reverse("authentik_api:oauthsource-detail", kwargs={"slug": bare.slug}),
            {"name": "renamed"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
