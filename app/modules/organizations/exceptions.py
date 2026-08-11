"""Custom exceptions for the `organizations` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: organizations, membership, and role-based permissions (owner/admin/member).
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class OrganizationNotFoundError(AppException):
    code = "ORGANIZATION_NOT_FOUND"
    message = "Organization not found."
    status_code = status.HTTP_404_NOT_FOUND


class NotOrganizationMemberError(AppException):
    code = "NOT_ORGANIZATION_MEMBER"
    message = "You are not a member of this organization."
    status_code = status.HTTP_403_FORBIDDEN


class AlreadyMemberError(AppException):
    code = "ALREADY_MEMBER"
    message = "This user is already a member of the organization."
    status_code = status.HTTP_409_CONFLICT


class MemberUserNotFoundError(AppException):
    code = "MEMBER_USER_NOT_FOUND"
    message = "No registered user found with that email."
    status_code = status.HTTP_404_NOT_FOUND


class MembershipNotFoundError(AppException):
    code = "MEMBERSHIP_NOT_FOUND"
    message = "Membership not found."
    status_code = status.HTTP_404_NOT_FOUND


class LastOwnerError(AppException):
    code = "LAST_OWNER"
    message = "Cannot change role: organization must keep at least one owner."
    status_code = status.HTTP_409_CONFLICT


class InsufficientPermissionError(AppException):
    code = "INSUFFICIENT_PERMISSION"
    message = "You do not have permission to perform this action."
    status_code = status.HTTP_403_FORBIDDEN
