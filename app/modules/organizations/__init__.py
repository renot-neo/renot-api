"""Module `organizations` - the service interface exposed to other modules.

Scope: organizations, membership, and role-based permissions (owner/admin/member).

Cross-module communication MUST go through the interface exposed here - other
modules MUST NOT `from app.modules.organizations.model import X` directly.
"""

from __future__ import annotations

from app.modules.organizations.model import Organization, OrganizationMembership, OrganizationRole
from app.modules.organizations.service import get_membership, has_permission, is_organization_active

__all__ = [
    "get_membership",
    "has_permission",
    "is_organization_active",
    "Organization",
    "OrganizationMembership",
    "OrganizationRole",
]
