import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_core", "0057_remove_user_groups_remove_user_user_permissions_and_more"),
        ("authentik_providers_scim", "0019_scimprovider_group_filters_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scimprovider",
            name="group_scope_root",
            field=models.ForeignKey(
                blank=True,
                default=None,
                help_text=(
                    "When set, sync only this group's descendants, never the group itself, and "
                    "only users in it or in its descendants."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="authentik_core.group",
            ),
        ),
    ]
