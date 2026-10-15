from django.db.models import Model
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from authentik.core.models import (
    USER_PATH_SYSTEM_PREFIX,
    Group,
    Token,
    TokenIntents,
    User,
    UserTypes,
)
from authentik.events.middleware import audit_ignore
from authentik.sources.scim.models import SCIMSource, SCIMSourceUser

USER_PATH_SOURCE_SCIM = USER_PATH_SYSTEM_PREFIX + "/sources/scim"


@receiver(post_save, sender=SCIMSource)
def scim_source_post_save(sender: type[Model], instance: SCIMSource, created: bool, **_):
    """Create service account before source is saved"""
    identifier = instance.service_account_identifier
    user, _ = User.objects.update_or_create(
        username=identifier,
        defaults={
            "name": f"SCIM Source {instance.name} Service-Account",
            "type": UserTypes.INTERNAL_SERVICE_ACCOUNT,
            "path": USER_PATH_SOURCE_SCIM,
        },
    )
    token, token_created = Token.objects.update_or_create(
        identifier=identifier,
        defaults={
            "user": user,
            "intent": TokenIntents.INTENT_API,
            "expiring": False,
            "managed": f"goauthentik.io/sources/scim/{instance.pk}",
        },
    )
    if created or token_created:
        with audit_ignore():
            instance.token = token
            instance.save()


@receiver(post_delete, sender=SCIMSource)
def scim_source_post_delete(sender: type[Model], instance: SCIMSource, **_):
    """Delete SCIM Source service account after deleting source"""
    User.objects.filter(
        username=instance.service_account_identifier, type=UserTypes.INTERNAL_SERVICE_ACCOUNT
    ).delete()


@receiver(pre_delete, sender=SCIMSourceUser)
def scim_source_user_pre_delete(sender: type[Model], instance: SCIMSourceUser, **_):
    """Unseat a user from its source's group when its connection goes"""
    group_id = (
        SCIMSource.objects.filter(pk=instance.source_id).values_list("group_id", flat=True).first()
    )
    if group_id:
        Group.users.through.objects.filter(group_id=group_id, user_id=instance.user_id).delete()
