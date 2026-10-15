import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_core", "0057_remove_user_groups_remove_user_user_permissions_and_more"),
        ("authentik_sources_scim", "0003_alter_scimsourcegroup_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scimsource",
            name="group",
            field=models.ForeignKey(
                blank=True,
                default=None,
                help_text=(
                    "Group every user the source provisions is seated in, and every group it "
                    "creates is nested under."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="authentik_core.group",
            ),
        ),
    ]
