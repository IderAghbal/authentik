from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_sources_scim", "fork_0001_one_source_per_object"),
        ("authentik_sources_scim", "fork_0002_scimsource_group"),
    ]

    operations = []
