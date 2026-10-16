"""Test that a full sync dispatches its pages and ends, holding its lease until they settle"""

from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.urls import reverse
from dramatiq.broker import get_broker
from rest_framework.test import APITestCase

from authentik.core.models import Application, Group, User
from authentik.core.tests.utils import create_test_admin_user
from authentik.lib.generators import generate_id
from authentik.lib.sync.outgoing.signals import sync_outgoing_inhibit_dispatch
from authentik.lib.sync.outgoing.tasks import SyncTasks
from authentik.lib.utils.reflection import class_to_path
from authentik.providers.scim.models import SCIMProvider
from authentik.providers.scim.tasks import scim_sync


class Dispatched:
    """Stands for dramatiq's group: keeps the pages a full sync dispatches, which the
    test broker would otherwise run inside the dispatch"""

    def __init__(self):
        self.pages = []

    def __call__(self, messages):
        self.pages.extend(messages)
        return self

    def run(self):
        return self

    def take(self):
        pages, self.pages = self.pages, []
        return pages


def run(message):
    """A page the full sync dispatched, run by the test broker as a message of its own"""
    get_broker().enqueue(message.copy(message_id=str(uuid4())))


class TestSCIMSyncNoWait(APITestCase):
    """A full sync never holds a worker thread while its pages wait for one"""

    def setUp(self) -> None:
        cache.clear()
        self.provider = SCIMProvider.objects.create(
            name=generate_id(),
            url="https://localhost",
            token=generate_id(),
            exclude_users_service_account=True,
            sync_page_size=1,
        )
        app = Application.objects.create(name=generate_id(), slug=generate_id())
        app.backchannel_providers.add(self.provider)
        with sync_outgoing_inhibit_dispatch():
            for _ in range(3):
                User.objects.create(username=generate_id())
            for _ in range(2):
                Group.objects.create(name=generate_id())
        self.client.force_login(create_test_admin_user())

    def running(self) -> bool:
        response = self.client.get(
            reverse("authentik_api:scimprovider-sync-status", kwargs={"pk": self.provider.pk})
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["is_running"]

    def test_a_full_sync_ends_and_its_last_page_releases_it(self):
        """The full sync ends having dispatched its users' pages; a second one meanwhile
        returns at once; the last users' page dispatches the groups', and the last of
        those releases the lease, however often a page runs and whether it fails"""
        dispatched, ran = Dispatched(), []
        users = self.provider.get_paginator(User).num_pages
        groups = self.provider.get_paginator(Group).num_pages
        failing = (class_to_path(Group), 1)

        def page(object_type, page, *args, **kwargs):
            ran.append((object_type, page))
            if (object_type, page) == failing:
                raise ValueError("a page that fails")

        with (
            patch("authentik.lib.sync.outgoing.tasks.group", dispatched),
            patch.object(SyncTasks, "sync_page", side_effect=page),
        ):
            scim_sync.send(self.provider.pk)
            pages = dispatched.take()
            self.assertEqual(len(pages), users)
            self.assertEqual(ran, [])
            self.assertTrue(self.running())

            scim_sync.send(self.provider.pk)
            self.assertEqual(dispatched.take(), [])

            for message in [pages[0], *pages[:-1]]:
                run(message)
            self.assertEqual(dispatched.take(), [])
            self.assertTrue(self.running())
            run(pages[-1])
            pages = dispatched.take()
            self.assertEqual(len(pages), groups)
            self.assertTrue(self.running())

            for message in pages:
                run(message)
            self.assertIn(failing, ran)
            self.assertFalse(self.running())

            scim_sync.send(self.provider.pk)
            self.assertEqual(len(dispatched.take()), users)
