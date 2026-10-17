"""An OAuth source updated in part keeps what the update leaves out"""

from django.urls import reverse
from requests_mock import Mocker
from rest_framework.test import APITestCase

from authentik.core.tests.utils import create_test_admin_user
from authentik.lib.generators import generate_id
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.google import GoogleType

WELL_KNOWN = "https://idp.example.com/.well-known/openid-configuration"
JWKS = "https://idp.example.com/jwks"
MOVED = "https://moved.example.com/.well-known/openid-configuration"


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
            name=generate_id(),
            slug=generate_id(),
            provider_type="openidconnect",
            consumer_key=generate_id(),
        )
        response = self.client.patch(
            reverse("authentik_api:oauthsource-detail", kwargs={"slug": bare.slug}),
            {"name": "renamed"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)


class TestPartialUpdateDiscovery(APITestCase):
    """Discovery fills only what neither the update nor the source sets: a
    PATCH never trades what a source holds for what a document answers."""

    def setUp(self):
        self.client.force_login(create_test_admin_user())

    def stored(self, provider_type: str) -> OAuthSource:
        """A source holding its own URLs, keys and method. A Google source's
        type also names a well-known and a JWKS URL of its own."""
        return OAuthSource.objects.create(
            name=generate_id(),
            slug=generate_id(),
            provider_type=provider_type,
            authorization_url="https://idp.example.com/authorize",
            access_token_url="https://idp.example.com/token",
            profile_url="https://idp.example.com/userinfo",
            oidc_well_known_url=WELL_KNOWN,
            oidc_jwks_url=JWKS,
            oidc_jwks={"keys": [{"kid": "stored"}]},
            pkce="S256",
            consumer_key=generate_id(),
            consumer_secret=generate_id(),
        )

    def documents(self, mocker: Mocker):
        """Every document answers values no source holds."""
        for well_known, host in [
            (WELL_KNOWN, "https://idp.example.com/answered"),
            (MOVED, "https://moved.example.com"),
            (GoogleType.oidc_well_known_url, "https://google.example.com"),
        ]:
            mocker.get(
                well_known,
                json={
                    "issuer": host,
                    "authorization_endpoint": f"{host}/authorize",
                    "token_endpoint": f"{host}/token",
                    "userinfo_endpoint": f"{host}/userinfo",
                    "jwks_uri": f"{host}/jwks",
                    "code_challenge_methods_supported": ["plain"],
                },
            )
        for jwks in [
            JWKS,
            "https://idp.example.com/answered/jwks",
            "https://moved.example.com/jwks",
            "https://google.example.com/jwks",
            GoogleType.oidc_jwks_url,
            "https://idp.example.com/rotated-jwks",
        ]:
            mocker.get(jwks, json={"keys": [{"kid": jwks}]})

    def patch(self, source: OAuthSource, data: dict) -> OAuthSource:
        with Mocker() as mocker:
            self.documents(mocker)
            response = self.client.patch(
                reverse("authentik_api:oauthsource-detail", kwargs={"slug": source.slug}),
                data,
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        source.refresh_from_db()
        return source

    def test_a_name_only_patch_keeps_what_the_source_holds(self):
        for provider_type in ["openidconnect", "google"]:
            with self.subTest(provider_type):
                source = self.patch(self.stored(provider_type), {"name": "renamed"})
                self.assertEqual(source.name, "renamed")
                self.assertEqual(source.authorization_url, "https://idp.example.com/authorize")
                self.assertEqual(source.access_token_url, "https://idp.example.com/token")
                self.assertEqual(source.profile_url, "https://idp.example.com/userinfo")
                self.assertEqual(source.oidc_well_known_url, WELL_KNOWN)
                self.assertEqual(source.oidc_jwks_url, JWKS)
                self.assertEqual(source.oidc_jwks, {"keys": [{"kid": "stored"}]})
                self.assertEqual(source.pkce, "S256")

    def test_a_patch_naming_one_endpoint_changes_it_alone(self):
        for provider_type in ["openidconnect", "google"]:
            with self.subTest(provider_type):
                source = self.patch(
                    self.stored(provider_type), {"profile_url": "https://idp.example.com/me"}
                )
                self.assertEqual(source.profile_url, "https://idp.example.com/me")
                self.assertEqual(source.authorization_url, "https://idp.example.com/authorize")
                self.assertEqual(source.access_token_url, "https://idp.example.com/token")
                self.assertEqual(source.oidc_jwks_url, JWKS)
                self.assertEqual(source.oidc_jwks, {"keys": [{"kid": "stored"}]})

    def test_a_patch_naming_the_stored_well_known_url_changes_nothing_else(self):
        source = self.patch(self.stored("openidconnect"), {"oidc_well_known_url": WELL_KNOWN})
        self.assertEqual(source.authorization_url, "https://idp.example.com/authorize")
        self.assertEqual(source.access_token_url, "https://idp.example.com/token")
        self.assertEqual(source.profile_url, "https://idp.example.com/userinfo")
        self.assertEqual(source.oidc_jwks_url, JWKS)
        self.assertEqual(source.oidc_jwks, {"keys": [{"kid": "stored"}]})
        self.assertEqual(source.pkce, "S256")

    def test_a_patch_moving_to_another_document_takes_what_it_derives(self):
        """A source moved to another document, an IdP's move or a document
        reissued after a key rotation, verifies against the new one."""
        source = self.patch(self.stored("openidconnect"), {"oidc_well_known_url": MOVED})
        self.assertEqual(source.authorization_url, "https://moved.example.com/authorize")
        self.assertEqual(source.access_token_url, "https://moved.example.com/token")
        self.assertEqual(source.profile_url, "https://moved.example.com/userinfo")
        self.assertEqual(source.oidc_jwks_url, "https://moved.example.com/jwks")
        self.assertEqual(source.oidc_jwks, {"keys": [{"kid": "https://moved.example.com/jwks"}]})

    def test_a_patch_moving_to_another_document_keeps_what_it_names(self):
        source = self.patch(
            self.stored("openidconnect"),
            {
                "oidc_well_known_url": MOVED,
                "profile_url": "https://idp.example.com/me",
                "oidc_jwks": {"keys": [{"kid": "pinned"}]},
            },
        )
        self.assertEqual(source.profile_url, "https://idp.example.com/me")
        self.assertEqual(source.authorization_url, "https://moved.example.com/authorize")
        self.assertEqual(source.access_token_url, "https://moved.example.com/token")
        self.assertEqual(source.oidc_jwks_url, "https://moved.example.com/jwks")
        self.assertEqual(source.oidc_jwks, {"keys": [{"kid": "pinned"}]})

    def test_a_patch_naming_a_jwks_url_takes_its_keys(self):
        """Another URL, or the stored one again: a URL the update names is
        read for its keys."""
        rotated = "https://idp.example.com/rotated-jwks"
        for url in [rotated, JWKS]:
            with self.subTest(url):
                source = self.patch(self.stored("openidconnect"), {"oidc_jwks_url": url})
                self.assertEqual(source.oidc_jwks_url, url)
                self.assertEqual(source.oidc_jwks, {"keys": [{"kid": url}]})

    def test_a_typed_source_fills_from_its_own_urls_not_its_types(self):
        """A source of a type with default URLs, given its own, fills an
        empty field from its own document and its own keys URL."""
        source = OAuthSource.objects.create(
            name=generate_id(),
            slug=generate_id(),
            provider_type="google",
            authorization_url="https://idp.example.com/authorize",
            access_token_url="https://idp.example.com/token",
            profile_url="",
            oidc_well_known_url=WELL_KNOWN,
            oidc_jwks_url=JWKS,
            consumer_key=generate_id(),
            consumer_secret=generate_id(),
        )
        source = self.patch(source, {"name": "renamed"})
        self.assertEqual(source.authorization_url, "https://idp.example.com/authorize")
        self.assertEqual(source.profile_url, "https://idp.example.com/answered/userinfo")
        self.assertEqual(source.oidc_well_known_url, WELL_KNOWN)
        self.assertEqual(source.oidc_jwks_url, JWKS)
        self.assertEqual(source.oidc_jwks, {"keys": [{"kid": JWKS}]})

    def test_a_new_source_is_filled_from_discovery(self):
        with Mocker() as mocker:
            self.documents(mocker)
            response = self.client.post(
                reverse("authentik_api:oauthsource-list"),
                {
                    "name": generate_id(),
                    "slug": "discovered",
                    "provider_type": "openidconnect",
                    "consumer_key": generate_id(),
                    "consumer_secret": generate_id(),
                    "oidc_well_known_url": WELL_KNOWN,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        source = OAuthSource.objects.get(slug="discovered")
        self.assertEqual(source.authorization_url, "https://idp.example.com/answered/authorize")
        self.assertEqual(source.access_token_url, "https://idp.example.com/answered/token")
        self.assertEqual(source.profile_url, "https://idp.example.com/answered/userinfo")
        self.assertEqual(source.oidc_jwks_url, "https://idp.example.com/answered/jwks")
        self.assertEqual(
            source.oidc_jwks, {"keys": [{"kid": "https://idp.example.com/answered/jwks"}]}
        )
