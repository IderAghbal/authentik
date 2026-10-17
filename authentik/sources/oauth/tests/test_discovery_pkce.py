"""OIDC discovery takes one PKCE method from the IdP's list"""

from django.test import TestCase
from requests_mock import Mocker

from authentik.lib.generators import generate_id
from authentik.sources.oauth.api.source import OAuthSourceSerializer
from authentik.sources.oauth.models import PKCEMethod

WELL_KNOWN = "https://idp.example.com/.well-known/openid-configuration"


class TestDiscoveryPKCE(TestCase):
    """The discovery document lists the methods an IdP supports; the source
    takes the strongest of them, never the list."""

    def validated(self, methods: list[str] | None, **data) -> dict:
        config = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "userinfo_endpoint": "https://idp.example.com/userinfo",
        }
        if methods is not None:
            config["code_challenge_methods_supported"] = methods
        with Mocker() as mocker:
            mocker.get(WELL_KNOWN, json=config)
            serializer = OAuthSourceSerializer(
                data={
                    "name": generate_id(),
                    "slug": generate_id(),
                    "provider_type": "openidconnect",
                    "consumer_key": generate_id(),
                    "consumer_secret": generate_id(),
                    "oidc_well_known_url": WELL_KNOWN,
                    "oidc_jwks_url": "",
                    **data,
                }
            )
            self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def test_s256_when_the_idp_supports_it(self):
        self.assertEqual(self.validated(["plain", "S256"])["pkce"], PKCEMethod.S256)

    def test_plain_when_it_is_all_the_idp_supports(self):
        self.assertEqual(self.validated(["plain"])["pkce"], PKCEMethod.PLAIN)

    def test_none_when_the_idp_lists_none(self):
        self.assertEqual(self.validated(None)["pkce"], PKCEMethod.NONE)
        self.assertEqual(self.validated([])["pkce"], PKCEMethod.NONE)

    def test_a_method_the_source_names_is_kept(self):
        self.assertEqual(self.validated(["plain", "S256"], pkce=PKCEMethod.PLAIN)["pkce"], PKCEMethod.PLAIN)
