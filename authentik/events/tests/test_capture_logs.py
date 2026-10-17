"""Test that a log capture holds what its own thread logs, beside captures in other threads"""

from threading import Event, Thread

from django.db import connection
from django.test import TransactionTestCase
from structlog.stdlib import get_logger

from authentik.blueprints.models import BlueprintInstance
from authentik.blueprints.v1.tasks import apply_blueprint
from authentik.events.logs import capture_logs
from authentik.lib.generators import generate_id
from authentik.tasks.models import TaskLog

LOGGER = get_logger()
# A flow without the name and title it needs: its blueprint fails validation.
INVALID = """version: 1
entries:
  - model: authentik_flows.flow
    identifiers:
      slug: {slug}
    attrs:
      designation: authentication
"""


class TestCaptureLogs(TransactionTestCase):
    """Workers apply blueprints and run other tasks on threads at once"""

    def test_a_capture_holds_only_what_its_own_thread_logs(self):
        """A capture opened while another thread holds one, and still open after that one
        closes, holds its own thread's entries, all of them, and nothing of the other's"""
        opened, logged, closed = Event(), Event(), Event()
        other = []

        def first():
            with capture_logs() as logs:
                opened.set()
                logged.wait(30)
                LOGGER.warning("first")
            other.extend(logs)
            closed.set()

        thread = Thread(target=first)
        thread.start()
        self.assertTrue(opened.wait(30))
        with capture_logs() as logs:
            LOGGER.warning("second, before")
            logged.set()
            self.assertTrue(closed.wait(30))
            LOGGER.warning("second, after")
        thread.join(30)
        self.assertEqual([log.event for log in other], ["first"])
        self.assertEqual([log.event for log in logs], ["second, before", "second, after"])

    def test_a_failing_apply_records_its_validation_warnings(self):
        """An apply that fails validation while another thread captures logs keeps the
        warnings of the failure in its task's log"""
        opened, release = Event(), Event()

        def holding():
            try:
                with capture_logs():
                    opened.set()
                    release.wait(60)
            finally:
                connection.close()

        instance = BlueprintInstance.objects.create(
            name=generate_id(), path="", content=INVALID.format(slug=generate_id())
        )
        thread = Thread(target=holding)
        thread.start()
        try:
            self.assertTrue(opened.wait(30))
            apply_blueprint.send_with_options(args=(instance.pk,), rel_obj=instance)
        finally:
            release.set()
            thread.join(60)
        events = TaskLog.objects.filter(task__rel_obj_id=str(instance.pk)).values_list(
            "event", flat=True
        )
        self.assertIn("Blueprint validation failed", list(events))
