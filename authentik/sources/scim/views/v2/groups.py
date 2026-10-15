"""SCIM Group Views"""

from typing import Any
from uuid import uuid4

from django.db.models import Q
from django.db.transaction import atomic
from django.http import QueryDict
from django.urls import reverse
from pydantic import ValidationError as PydanticValidationError
from pydanticscim.group import GroupMember
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from scim2_filter_parser.attr_paths import AttrPath

from authentik.core.models import Group, User
from authentik.providers.scim.clients.schema import SCIM_GROUP_SCHEMA, PatchOp, PatchOperation
from authentik.providers.scim.clients.schema import Group as SCIMGroupModel
from authentik.sources.scim.models import SCIMSourceGroup, seating_refusal
from authentik.sources.scim.patch.parser import SCIMPathParser
from authentik.sources.scim.patch.processor import SCIMPatchProcessor
from authentik.sources.scim.views.v2.base import SCIMObjectView
from authentik.sources.scim.views.v2.exceptions import (
    SCIMConflictError,
    SCIMForbiddenError,
    SCIMNotFoundError,
    SCIMValidationError,
)


class GroupsView(SCIMObjectView):
    """SCIM Group view"""

    model = Group

    def group_to_scim(self, scim_group: SCIMSourceGroup) -> dict:
        """Convert Group to SCIM data"""
        payload = SCIMGroupModel(
            schemas=[SCIM_GROUP_SCHEMA],
            id=str(scim_group.group.pk),
            externalId=scim_group.external_id,
            displayName=self.unnamespaced(scim_group.group.name),
            members=[],
            meta={
                "resourceType": "Group",
                "lastModified": scim_group.last_update,
                "location": self.request.build_absolute_uri(
                    reverse(
                        "authentik_sources_scim:v2-groups",
                        kwargs={
                            "source_slug": self.kwargs["source_slug"],
                            "group_id": str(scim_group.group.pk),
                        },
                    )
                ),
            },
        )
        for member in scim_group.group.users.order_by("pk"):
            member: User
            payload.members.append(GroupMember(value=str(member.uuid)))
        final_payload = payload.model_dump(mode="json", exclude_unset=True)
        final_payload.update(scim_group.attributes)
        return self.remove_excluded_attributes(
            SCIMGroupModel.model_validate(final_payload).model_dump(mode="json", exclude_unset=True)
        )

    def get(self, request: Request, group_id: str | None = None, **kwargs) -> Response:
        """List Group handler"""
        base_query = SCIMSourceGroup.objects.select_related("group").prefetch_related(
            "group__users"
        )
        if group_id:
            connection = base_query.filter(source=self.source, group__group_uuid=group_id).first()
            if not connection:
                raise SCIMNotFoundError("Group not found.")
            return Response(self.group_to_scim(connection))
        connections = (
            base_query.filter(source=self.source).order_by("pk").filter(self.filter_parse(request))
        )
        page = self.paginate_query(connections)
        return Response(
            {
                "totalResults": page.paginator.count,
                "itemsPerPage": page.paginator.per_page,
                "startIndex": page.start_index(),
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "Resources": [self.group_to_scim(connection) for connection in page],
            }
        )

    @atomic
    def update_group(self, connection: SCIMSourceGroup | None, data: QueryDict, apply_members=True):
        """Partial update a group"""
        properties = self.build_object_properties(data)

        if not properties.get("name"):
            raise ValidationError("Invalid group")
        if self.source.group_id and (refusal := seating_refusal(self.source.group)):
            raise SCIMForbiddenError(refusal)
        properties["name"] = self.namespaced(properties["name"])

        group = connection.group if connection else Group()
        if Group.objects.filter(name=properties["name"]).exclude(pk=group.pk).exists():
            raise SCIMConflictError("displayName is already in use.")

        group.update_attributes(properties)
        if not connection and self.source.group_id and group.pk != self.source.group_id:
            group.parents.add(self.source.group_id)

        if "members" in data and apply_members:
            self.set_members(group, self.member_query(data.get("members") or []))
        data["members"] = self._convert_members(group)
        if not connection:
            connection, _ = SCIMSourceGroup.objects.update_or_create(
                external_id=data.get("externalId") or str(uuid4()),
                source=self.source,
                group=group,
                defaults={
                    "attributes": data,
                },
            )
        else:
            connection.external_id = data.get("externalId", connection.external_id)
            connection.attributes = data
            connection.save()
        return connection

    def post(self, request: Request, **kwargs) -> Response:
        """Create group handler"""
        match = Q(group__group_uuid=request.data.get("id")) | Q(
            group__name=self.namespaced(request.data.get("displayName") or "")
        )
        if request.data.get("externalId"):
            match |= Q(external_id=request.data.get("externalId"))
        connection = SCIMSourceGroup.objects.filter(match, source=self.source).first()
        if connection:
            self.logger.debug("Found existing group")
            raise SCIMConflictError("Group with ID exists already.")
        connection = self.update_group(None, request.data)
        return Response(self.group_to_scim(connection), status=201)

    def put(self, request: Request, group_id: str, **kwargs) -> Response:
        """Update group handler"""
        connection = SCIMSourceGroup.objects.filter(
            source=self.source, group__group_uuid=group_id
        ).first()
        if not connection:
            raise SCIMNotFoundError("Group not found.")
        connection = self.update_group(connection, request.data)
        return Response(self.group_to_scim(connection), status=200)

    def own_users(self, query: Q):
        """The users query names that this source created. A member another
        source created, or none did, is ignored."""
        users = User.objects.filter(query)
        own = users.filter(scimsourceuser__source=self.source)
        if ignored := users.exclude(pk__in=own.values("pk")).count():
            self.logger.warning("Ignoring members this source did not create", count=ignored)
        return own

    def filtered_member(self, operation: PatchOperation) -> str | None:
        """The member a remove by the filter form `members[value eq "<id>"]`
        names, or None for any other operation."""
        if operation.op.lower() != "remove" or not (operation.path or "").startswith("members["):
            return None
        components = SCIMPathParser().parse_path(operation.path)
        if len(components) != 1 or components[0]["sub_attribute"]:
            return None
        condition = components[0]["filter"] or {}
        if (
            condition.get("type") == "comparison"
            and condition.get("attribute") == "value"
            and condition.get("operator", "").lower() == "eq"
        ):
            return condition.get("value")
        return None

    def member_query(self, members: Any) -> Q:
        """The users a members value names, one member or a list of them,
        skipping any entry that is not a member"""
        query = Q()
        for _member in members if isinstance(members, list) else [members]:
            try:
                member = GroupMember.model_validate(_member)
            except PydanticValidationError as exc:
                self.logger.warning("Invalid group member", exc=exc)
                continue
            query |= Q(uuid=member.value)
        return query

    def set_members(self, group: Group, query: Q):
        """Make the users query names the group's members, none when it names none"""
        group.users.set(self.own_users(query) if query else [])

    def patch_members(self, group: Group, op: PatchOp, value: Any):
        """Apply one PATCH operation to the group's members (RFC 7644 3.5.2):
        add seats the users named, replace makes them the members, and remove
        unseats them or, naming none, every member"""
        if op == PatchOp.remove and value is None:
            group.users.clear()
            return
        query = self.member_query(value)
        if op == PatchOp.replace:
            self.set_members(group, query)
        elif query and op == PatchOp.add:
            group.users.add(*self.own_users(query))
        elif query and op == PatchOp.remove:
            group.users.remove(*self.own_users(query))

    def _convert_members(self, group: Group):
        users = []
        for user in group.users.all().order_by("uuid"):
            users.append({"value": str(user.uuid)})
        return sorted(users, key=lambda u: u["value"])

    @atomic
    def patch(self, request: Request, group_id: str, **kwargs) -> Response:
        """Patch group handler"""
        connection = SCIMSourceGroup.objects.filter(
            source=self.source, group__group_uuid=group_id
        ).first()
        if not connection:
            raise SCIMNotFoundError("Group not found.")
        if self.source.group_id and (refusal := seating_refusal(self.source.group)):
            raise SCIMForbiddenError(refusal)

        operations = []
        for _op in request.data.get("Operations", []):
            operation = PatchOperation.model_validate(_op)
            if operation.op.lower() not in ["add", "remove", "replace"]:
                raise SCIMValidationError()
            if member := self.filtered_member(operation):
                connection.group.users.remove(*self.own_users(Q(uuid=member)))
                continue
            operations.append(_op)
            if operation.path is None:
                # A pathless add or replace names attributes with their values
                # (RFC 7644 3.5.2.1, 3.5.2.3); a remove needs a path.
                if (
                    operation.op != PatchOp.remove
                    and isinstance(operation.value, dict)
                    and "members" in operation.value
                ):
                    self.patch_members(connection.group, operation.op, operation.value["members"])
            elif AttrPath(f'{operation.path} eq ""', {}).first_path == ("members", None, None):
                self.patch_members(connection.group, operation.op, operation.value)
        patcher = SCIMPatchProcessor()
        patched_data = patcher.apply_patches(connection.attributes, operations)
        patched_data["members"] = self._convert_members(connection.group)
        if patched_data != connection.attributes:
            self.update_group(connection, patched_data, apply_members=False)
        return Response(self.group_to_scim(connection), status=200)

    @atomic
    def delete(self, request: Request, group_id: str, **kwargs) -> Response:
        """Delete group handler"""
        connection = SCIMSourceGroup.objects.filter(
            source=self.source, group__group_uuid=group_id
        ).first()
        if not connection:
            raise SCIMNotFoundError("Group not found.")
        connection.group.delete()
        connection.delete()
        return Response(status=204)
