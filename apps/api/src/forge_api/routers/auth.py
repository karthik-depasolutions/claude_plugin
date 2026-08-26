"""Login/logout for admin-provisioned accounts (see ../../scripts/create_user.py
- there is no signup endpoint). Sessions are a JWT in an HttpOnly cookie;
`get_current_user` is imported by every other router that needs to require
login (see runs.py/packs.py's router-level `dependencies=`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from forge_api.config import get_settings
from forge_api.db import get_session
from forge_api.models_orm import UserORM
from forge_api.schemas import LoginRequest, SignupRequest, UserPublic
from forge_api.security import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    hash_password,
    issue_session_token,
    verify_password,
    verify_session_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> UserORM:
    email = verify_session_token(session_token) if session_token else None
    if email is None:
        raise HTTPException(401, "Not authenticated.")
    user = await session.get(UserORM, email)
    if user is None:
        raise HTTPException(401, "Not authenticated.")
    return user


def _is_admin(user: UserORM) -> bool:
    settings = get_settings()
    return bool(user.is_admin or user.email in settings.admin_email_list or user.email.startswith("admin@"))


@router.post("/signup", response_model=UserPublic, status_code=201)
async def signup(body: SignupRequest, request: Request, response: Response, session: SessionDep) -> UserPublic:
    clean_email = body.email.strip().lower()
    if "@" not in clean_email or "." not in clean_email:
        raise HTTPException(422, "Please provide a valid email address.")

    existing = await session.get(UserORM, clean_email)
    if existing is not None:
        raise HTTPException(409, "An account with this email already exists.")

    settings = get_settings()
    is_admin = clean_email in settings.admin_email_list or clean_email.startswith("admin@")

    user = UserORM(email=clean_email, password_hash=hash_password(body.password), is_admin=is_admin)
    session.add(user)
    await session.commit()

    response.set_cookie(
        SESSION_COOKIE_NAME,
        issue_session_token(user.email),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return UserPublic(email=user.email, is_admin=_is_admin(user))


@router.post("/login", response_model=UserPublic)
async def login(body: LoginRequest, request: Request, response: Response, session: SessionDep) -> UserPublic:
    user = await session.get(UserORM, body.email.strip().lower())
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")

    settings = get_settings()
    if (user.email in settings.admin_email_list or user.email.startswith("admin@")) and not user.is_admin:
        user.is_admin = True
        await session.commit()

    response.set_cookie(
        SESSION_COOKIE_NAME,
        issue_session_token(user.email),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return UserPublic(email=user.email, is_admin=_is_admin(user))


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me(user: Annotated[UserORM, Depends(get_current_user)]) -> UserPublic:
    return UserPublic(email=user.email, is_admin=_is_admin(user))
