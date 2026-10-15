"""Test that a full sync's pages never hold back a single object's sync"""

from django.test import TestCase
from dramatiq import get_broker

from authentik.providers.scim import tasks

FULL_SYNC = (tasks.scim_sync, tasks.scim_sync_objects)
OBJECT_SYNCS = (
    tasks.scim_sync_direct,
    tasks.scim_sync_direct_dispatch,
    tasks.scim_sync_delete,
    tasks.scim_sync_delete_dispatch,
    tasks.scim_sync_m2m,
    tasks.scim_sync_m2m_dispatch,
)


class TestSCIMSyncQueue(TestCase):
    """A change to one user or group is not queued behind a full sync"""

    def test_full_sync_has_its_own_queue(self):
        """A full sync's pages fill a queue of their own, so a worker always
        fetches an object's sync while pages wait"""
        for full in FULL_SYNC:
            for single in OBJECT_SYNCS:
                with self.subTest(full=full.actor_name, single=single.actor_name):
                    self.assertNotEqual(full.queue_name, single.queue_name)
        self.assertIn(tasks.scim_sync_objects.queue_name, get_broker().get_declared_queues())

    def test_object_syncs_run_first(self):
        """A worker holding both runs the object's sync first"""
        for full in FULL_SYNC:
            for single in OBJECT_SYNCS:
                with self.subTest(full=full.actor_name, single=single.actor_name):
                    self.assertLess(single.priority, full.priority)
