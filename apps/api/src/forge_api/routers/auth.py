"""Login/logout for admin-provisioned accounts (see ../../scripts/create_user.py
- there is no signup endpoint). Sessions are a JWT in an HttpOnly cookie;
`get_current_user` is imported by every other router that needs to require
login (see runs.py/packs.py's router-level `dependencies=`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from forge_api.db import get_session
from forge_api.models_orm import UserORM
from forge_api.schemas import LoginRequest, UserPublic
from forge_api.security import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
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


@router.post("/login", response_model=UserPublic)
async def login(body: LoginRequest, request: Request, response: Response, session: SessionDep) -> UserPublic:
    user = await session.get(UserORM, body.email.strip().lower())
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issue_session_token(user.email),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return UserPublic(email=user.email)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me(user: Annotated[UserORM, Depends(get_current_user)]) -> UserPublic:
    return UserPublic(email=user.email)
