"""Test that a full sync requested while one runs is run after it"""

from threading import Event, Thread
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase

from authentik.core.models import Application
from authentik.lib.generators import generate_id
from authentik.lib.sync.outgoing.tasks import SyncTasks
from authentik.providers.scim.models import SCIMProvider
from authentik.providers.scim.tasks import scim_sync


class TestSCIMSyncRerun(TransactionTestCase):
    """A save during a full sync is not dropped"""

    def setUp(self) -> None:
        self.provider = SCIMProvider.objects.create(
            name=generate_id(),
            url="https://localhost",
            token=generate_id(),
            exclude_users_service_account=True,
        )
        app = Application.objects.create(name=generate_id(), slug=generate_id())
        app.backchannel_providers.add(self.provider)
        # The provider's schedules, which saving it creates outside tests
        for spec in self.provider.schedule_specs:
            spec.rel_obj = self.provider
            spec.identifier = self.provider.pk
            spec.update_or_create()

    def test_requested_during_a_sync_runs_after_it(self):
        """A full sync requested while another holds the lock runs once that
        one is done, and a sync requested with none running runs once"""
        running, release = Event(), Event()
        pages = []

        def paginate(*args, **kwargs):
            pages.append(kwargs["object_type"])
            running.set()
            release.wait(30)
            return []

        def first():
            try:
                scim_sync.send(self.provider.pk)
            finally:
                connection.close()

        with patch.object(SyncTasks, "sync_paginator", side_effect=paginate):
            thread = Thread(target=first)
            thread.start()
            self.assertTrue(running.wait(30))
            scim_sync.send(self.provider.pk)
            self.assertEqual(len(pages), 1)
            release.set()
            thread.join(60)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(pages), 4)
            scim_sync.send(self.provider.pk)
            self.assertEqual(len(pages), 6)
