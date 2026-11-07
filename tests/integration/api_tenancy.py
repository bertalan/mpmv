"""Test-only API v1 operations for the cross-tenant harness (CORE.13).

Built with the production `create_api()`. The correct operations reach vendor data through
`get_for_actor_or_404` / `for_actor` with the calling API client; the `leaky` and `forbidden`
ones make the classic mistakes, so the tests can prove that the harness catches each one when
the caller is another vendor's API key.
"""

import uuid

from django.http import HttpRequest
from ninja import Router

from apps.api.auth import api_client_of
from apps.api.v1.api import create_api
from apps.api.v1.errors import ApiProblem
from apps.api.v1.security import ApiKey
from apps.vendors.models import VendorMember
from apps.vendors.scoping import get_for_actor_or_404

SCOPE = "members:read"

router = Router(tags=["tenancy"])


def _member(member: VendorMember) -> dict[str, str]:
    return {"public_id": str(member.public_id), "user": member.user.email}


@router.get("/members/{public_id}", auth=ApiKey(SCOPE))
def member_detail(request: HttpRequest, public_id: uuid.UUID) -> dict[str, str]:
    return _member(get_for_actor_or_404(VendorMember, api_client_of(request), public_id=public_id))


@router.get("/leaky/members/{public_id}", auth=ApiKey(SCOPE))
def leaky_member_detail(request: HttpRequest, public_id: uuid.UUID) -> dict[str, str]:
    return _member(VendorMember.objects.get(public_id=public_id))  # no tenant filter


@router.get("/forbidden/members/{public_id}", auth=ApiKey(SCOPE))
def forbidden_member_detail(request: HttpRequest, public_id: uuid.UUID) -> dict[str, str]:
    member = VendorMember.objects.get(public_id=public_id)
    if member.vendor_id != api_client_of(request).vendor_id:
        raise ApiProblem(403, code="forbidden", detail="Not your vendor.")  # tells it exists
    return _member(member)


@router.get("/members", auth=ApiKey(SCOPE))
def member_list(request: HttpRequest) -> list[dict[str, str]]:
    members = VendorMember.objects.for_actor(api_client_of(request)).select_related("user")
    return [_member(member) for member in members]


@router.get("/leaky/members", auth=ApiKey(SCOPE))
def leaky_member_list(request: HttpRequest) -> list[dict[str, str]]:
    return [_member(member) for member in VendorMember.objects.select_related("user")]


tenancy_api = create_api(urls_namespace="api-v1-tenancy")
tenancy_api.add_router("", router)
