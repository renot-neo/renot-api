"""Router module `auth`.

Endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`,
`POST /auth/switch-organization`.

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.response import Envelope, success_envelope
from app.modules.auth import service
from app.modules.auth.model import User
from app.modules.auth.schema import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SwitchOrganizationRequest,
    TokenPairResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[UserResponse],
    summary="Register a new user",
    description="Creates a new user account with email + password. Does not "
    "automatically create an organization - see `POST /organizations`.",
)
async def register(
    data: RegisterRequest, request: Request, session: AsyncSession = Depends(get_db)
) -> dict:
    user = await service.register(
        session, email=data.email, password=data.password, full_name=data.full_name
    )
    await session.commit()
    return success_envelope(UserResponse.model_validate(user), request=request)


@router.post(
    "/login",
    response_model=Envelope[TokenPairResponse],
    summary="Login",
    description="Authenticates email + password, issues an access + refresh "
    "token pair. The token has no active tenant context yet - call "
    "`switch-organization` afterward.",
)
async def login(
    data: LoginRequest, request: Request, session: AsyncSession = Depends(get_db)
) -> dict:
    tokens = await service.login(session, email=data.email, password=data.password)
    await session.commit()
    return success_envelope(tokens, request=request)


@router.post(
    "/refresh",
    response_model=Envelope[TokenPairResponse],
    summary="Refresh access token",
    description="Exchanges a refresh token (still valid & not revoked) for a "
    "new token pair (the old refresh token is automatically revoked/rotated).",
)
async def refresh(
    data: RefreshRequest, request: Request, session: AsyncSession = Depends(get_db)
) -> dict:
    tokens = await service.refresh(session, refresh_token=data.refresh_token)
    await session.commit()
    return success_envelope(tokens, request=request)


@router.post(
    "/switch-organization",
    response_model=Envelope[TokenPairResponse],
    summary="Switch the active organization",
    description="Issues a new token with `tenant_id` set to the selected "
    "organization - the user must already be a member of it.",
)
async def switch_organization(
    data: SwitchOrganizationRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    tokens = await service.switch_organization(
        session, user=current_user, organization_id=data.organization_id
    )
    await session.commit()
    return success_envelope(tokens, request=request)
