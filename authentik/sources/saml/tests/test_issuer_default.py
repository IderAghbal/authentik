"""A SAML source made without an issuer answers with its metadata URL"""

from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APITestCase

from authentik.core.tests.utils import create_test_admin_user, create_test_flow
from authentik.lib.generators import generate_id
from authentik.sources.saml.models import SAMLSource


class TestIssuerDefault(APITestCase):
    """The issuer is optional and defaults to the source's metadata URL."""

    def setUp(self):
        self.client.force_login(create_test_admin_user())
        self.flow = create_test_flow()

    def test_a_source_made_without_an_issuer_takes_its_metadata_url(self):
        """A POST naming no issuer makes the source, which names its own
        metadata URL as its issuer."""
        slug = generate_id()
        response = self.client.post(
            reverse("authentik_api:samlsource-list"),
            {
                "name": generate_id(),
                "slug": slug,
                "sso_url": "https://idp.example.com/sso",
                "pre_authentication_flow": str(self.flow.pk),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        source = SAMLSource.objects.get(slug=slug)
        request = RequestFactory().get("/")
        self.assertEqual(
            source.get_issuer(request),
            request.build_absolute_uri(
                reverse("authentik_sources_saml:metadata", kwargs={"source_slug": slug})
            ),
        )

    def test_a_source_made_with_an_issuer_keeps_it(self):
        """An issuer the POST names is the issuer."""
        slug = generate_id()
        response = self.client.post(
            reverse("authentik_api:samlsource-list"),
            {
                "name": generate_id(),
                "slug": slug,
                "sso_url": "https://idp.example.com/sso",
                "pre_authentication_flow": str(self.flow.pk),
                "issuer": "https://sp.example.com",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        source = SAMLSource.objects.get(slug=slug)
        self.assertEqual(source.get_issuer(RequestFactory().get("/")), "https://sp.example.com")
