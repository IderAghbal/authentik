"""Blueprint applications take turns, and a blueprint applied first is applied once"""

from hashlib import sha512
from pathlib import Path
from tempfile import mkdtemp
from threading import Event, Thread

from django.db import connection
from django.db.transaction import atomic
from django.test import TransactionTestCase
from yaml import dump

from authentik.blueprints.models import BlueprintInstance, BlueprintInstanceStatus
from authentik.blueprints.v1.tasks import apply_blueprint
from authentik.flows.models import Flow
from authentik.lib.config import CONFIG
from authentik.lib.generators import generate_id

TMP = mkdtemp("authentik-blueprints")


def instance(content: dict) -> BlueprintInstance:
    """A blueprint file holding content, and its instance, never applied"""
    path = f"{generate_id()}.yaml"
    Path(TMP, path).write_text(dump(content), encoding="utf-8")
    return BlueprintInstance.objects.create(name=path, path=path)


def flow(slug: str) -> dict:
    """A blueprint making one flow, with the title it applies"""
    return {
        "version": 1,
        "entries": [
            {
                "model": "authentik_flows.flow",
                "identifiers": {"slug": slug},
                "attrs": {"name": slug, "title": "applied", "designation": "stage_configuration"},
            }
        ],
    }


def applying_first(target: BlueprintInstance) -> dict:
    """A blueprint that applies target first"""
    return {
        "version": 1,
        "entries": [
            {
                "model": "authentik_blueprints.metaapplyblueprint",
                "attrs": {"identifiers": {"path": target.path}, "required": True},
            }
        ],
    }


class TestBlueprintsAppliedOnce(TransactionTestCase):
    """A blueprint applied first by another, by the worker beside it or by two at
    once, is applied once"""

    @CONFIG.patch("blueprints_dir", TMP)
    def test_a_blueprint_applied_at_its_content_is_not_applied_again_first(self):
        slug = generate_id()
        target = instance(flow(slug))
        apply_blueprint.send(target.pk)
        Flow.objects.filter(slug=slug).update(title="changed")
        apply_blueprint.send(instance(applying_first(target)).pk)
        self.assertEqual(Flow.objects.get(slug=slug).title, "changed")

    @CONFIG.patch("blueprints_dir", TMP)
    def test_a_blueprint_applied_first_is_recorded_as_applied(self):
        slug = generate_id()
        target = instance(flow(slug))
        apply_blueprint.send(instance(applying_first(target)).pk)
        target.refresh_from_db()
        self.assertEqual(target.status, BlueprintInstanceStatus.SUCCESSFUL)
        self.assertEqual(target.last_applied_hash, sha512(target.retrieve().encode()).hexdigest())
        self.assertEqual(Flow.objects.get(slug=slug).title, "applied")

        # Discovery's application, which its file's change requested before, is
        # not run; one the API requests is.
        Flow.objects.filter(slug=slug).update(title="changed")
        apply_blueprint.send_with_options(args=(target.pk,), kwargs={"if_changed": True})
        self.assertEqual(Flow.objects.get(slug=slug).title, "changed")
        apply_blueprint.send(target.pk)
        self.assertEqual(Flow.objects.get(slug=slug).title, "applied")

    @CONFIG.patch("blueprints_dir", TMP)
    def test_an_application_waits_for_the_one_running(self):
        from authentik.blueprints.v1.importer import apply_lock

        slug = generate_id()
        target = instance(flow(slug))
        held, release, done = Event(), Event(), Event()

        def hold():
            try:
                with atomic():
                    apply_lock()
                    held.set()
                    release.wait(60)
            finally:
                connection.close()

        def apply():
            try:
                apply_blueprint.send(target.pk)
                done.set()
            finally:
                connection.close()

        holder = Thread(target=hold)
        holder.start()
        self.assertTrue(held.wait(60))
        applier = Thread(target=apply)
        applier.start()
        self.assertFalse(done.wait(3))
        self.assertFalse(Flow.objects.filter(slug=slug).exists())
        release.set()
        self.assertTrue(done.wait(60))
        holder.join()
        applier.join()
        self.assertTrue(Flow.objects.filter(slug=slug).exists())
