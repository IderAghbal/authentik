"""SCIM Utils"""

from typing import Any
from uuid import UUID

from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as rest_exception_handler
from scim2_filter_parser.lexer import SCIMLexer
from scim2_filter_parser.parser import SCIMParser as FilterParser
from scim2_filter_parser.parser import SCIMParserError
from scim2_filter_parser.transpilers.django_q_object import Transpiler
from structlog import BoundLogger
from structlog.stdlib import get_logger

from authentik.core.models import Group, User
from authentik.core.sources.mapper import SourceMapper
from authentik.lib.sync.mapper import PropertyMappingManager
from authentik.sources.scim.models import SCIMSource
from authentik.sources.scim.views.v2.auth import SCIMTokenAuth
from authentik.sources.scim.views.v2.exceptions import (
    SCIMError,
    SCIMErrorTypes,
    SCIMNotFoundError,
    SCIMNotImplementedError,
    SCIMValidationError,
)

SCIM_CONTENT_TYPE = "application/scim+json"


class FilterTranspiler(Transpiler):
    """Refuses an attribute the endpoint does not filter on, which the
    library's transpiler drops: dropped from an `and`, it widens the
    result instead of failing the filter."""

    def visit_AttrPath(self, node):
        mapped = super().visit_AttrPath(node)
        if mapped is None:
            raise ValueError("an attribute this endpoint does not filter on")
        return mapped


class SCIMParser(JSONParser):
    """SCIM clients use a custom content type"""

    media_type = SCIM_CONTENT_TYPE


class SCIMRenderer(JSONRenderer):
    """SCIM clients also expect a custom content type"""

    media_type = SCIM_CONTENT_TYPE


class SCIMView(APIView):
    """Base class for SCIM Views"""

    source: SCIMSource
    logger: BoundLogger

    permission_classes = [IsAuthenticated]
    parser_classes = [SCIMParser, JSONParser]
    renderer_classes = [SCIMRenderer]

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        self.logger = get_logger().bind()
        super().setup(request, *args, **kwargs)

    def get_authenticators(self):
        return [SCIMTokenAuth(self)]

    def get_exception_handler(self):
        return scim_exception_handler

    def remove_excluded_attributes(self, data: dict):
        """Remove attributes specified in excludedAttributes"""
        excluded: str = self.request.query_params.get("excludedAttributes", "")
        for key in excluded.split(","):
            data.pop(key.strip(), None)
        return data

    def filter_parse(self, request: Request):
        """Parse the path of a Patch Operation"""
        path = request.query_params.get("filter")
        if not path:
            return Q()
        attr_map = {}
        if self.model == User:
            attr_map = {
                ("userName", None, None): "attributes__userName",
                ("externalId", None, None): "external_id",
                ("active", None, None): "user__is_active",
                ("name", "familyName", None): "attributes__familyName",
            }
        elif self.model == Group:
            attr_map = {
                ("displayName", None, None): "attributes__displayName",
                ("externalId", None, None): "external_id",
                ("members", None, None): "group__users",
            }
        try:
            query = FilterTranspiler(attr_map).transpile(
                FilterParser().parse(SCIMLexer().tokenize(path))
            )
        except (SCIMParserError, ValueError, NotImplementedError):
            query = None
        if query is None:
            # RFC 7644 3.4.2.2: a filter the server cannot read is the
            # client's error.
            raise SCIMValidationError(
                SCIMError(scimType=SCIMErrorTypes.invalid_filter, detail="Invalid filter.", status=400)
            )
        return query

    def paginate_query(self, query: QuerySet) -> Page:
        per_page = int(self.request.tenant.pagination_default_page_size)
        start_index = 1
        try:
            start_index = int(self.request.query_params.get("startIndex", 1))
        except ValueError:
            pass
        paginator = Paginator(query, per_page=per_page)
        page = paginator.page(int(max(start_index / per_page, 1)))
        return page


class SCIMObjectView(SCIMView):
    """Base SCIM View for object management"""

    mapper: SourceMapper
    manager: PropertyMappingManager

    model: type[User | Group]

    def initial(self, request: Request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        # This needs to happen after authentication has happened, because we don't have
        # a source attribute before
        self.mapper = SourceMapper(self.source)
        self.manager = self.mapper.get_manager(self.model, ["data"])
        for key, value in kwargs.items():
            if key.endswith("_id"):
                try:
                    UUID(value)
                except ValueError:
                    raise SCIMNotFoundError("Invalid ID") from None

    def namespaced(self, name: str) -> str:
        """The name a user or group this source creates holds: the client's
        name under the source's slug. A slug holds no dot, so no two
        sources share a name."""
        return f"{self.source.slug}.{name}"

    def unnamespaced(self, name: str) -> str:
        """The client's own name for a user or group of this source."""
        return name.removeprefix(f"{self.source.slug}.")

    def build_object_properties(self, data: dict[str, Any]) -> dict[str, Any | dict[str, Any]]:
        return self.mapper.build_object_properties(
            object_type=self.model,
            manager=self.manager,
            user=None,
            request=self.request,
            data=data,
        )


def scim_exception_handler(exc: Exception, context: dict) -> Response:
    """Answer every error as a SCIM error (RFC 7644 3.12): the framework's
    own errors are reshaped, and an unhandled one answers 500 instead of
    rendering the HTML error page."""
    response = rest_exception_handler(exc, context)
    if response is None:
        get_logger().warning("Unhandled exception in SCIM request", exc=exc)
        response = Response(status=500)
        detail = "An internal error occurred."
    elif isinstance(response.data, dict) and "schemas" in response.data:
        return response
    else:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
    response.data = SCIMError(
        status=response.status_code, detail=str(detail) if detail else None
    ).model_dump(mode="json", exclude_none=True)
    return response


class SCIMUnknownView(SCIMView):
    """Any other path under a source's base URL: 501 for a search by POST,
    which is not supported (RFC 7644 3.4.3), and 404 otherwise."""

    def initial(self, request: Request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if kwargs.get("rest", "").rstrip("/").endswith(".search"):
            raise SCIMNotImplementedError("Search by POST is not supported.")
        raise SCIMNotFoundError("Not found.")


class SCIMRootView(SCIMView):
    """Root SCIM View"""

    def dispatch(self, request: Request, *args, **kwargs) -> Response:
        return Response({"message": "Use this base-URL with a SCIM-compatible system."})
