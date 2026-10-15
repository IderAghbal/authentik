"""A user or group belongs to at most one SCIM source.

Named outside the app's numbering so it never collides with a migration
of the same number; on every rebase its dependency moves to the app's
newest migration, so the app keeps one leaf."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_sources_scim", "0003_alter_scimsourcegroup_unique_together_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="scimsourceuser",
            constraint=models.UniqueConstraint(fields=["user"], name="scimsourceuser_one_source"),
        ),
        migrations.AddConstraint(
            model_name="scimsourcegroup",
            constraint=models.UniqueConstraint(fields=["group"], name="scimsourcegroup_one_source"),
        ),
    ]
