from __future__ import annotations

from httpx import AsyncClient


async def _create_user(email: str, password: str) -> None:
    from forge_api.db import session_factory
    from forge_api.models_orm import UserORM
    from forge_api.security import hash_password

    async with session_factory()() as session:
        session.add(UserORM(email=email, password_hash=hash_password(password)))
        await session.commit()


async def test_protected_route_401s_without_a_session_cookie(unauthenticated_client: AsyncClient):
    response = await unauthenticated_client.get("/runs")
    assert response.status_code == 401


async def test_login_rejects_unknown_email_and_wrong_password(unauthenticated_client: AsyncClient):
    await _create_user("owner@example.com", "correct horse battery staple")

    wrong_password = await unauthenticated_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "nope"}
    )
    assert wrong_password.status_code == 401

    unknown_user = await unauthenticated_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert unknown_user.status_code == 401


async def test_login_sets_a_cookie_that_authorizes_subsequent_requests(unauthenticated_client: AsyncClient):
    await _create_user("owner@example.com", "correct horse battery staple")

    login = await unauthenticated_client.post(
        "/auth/login", json={"email": "OWNER@example.com", "password": "correct horse battery staple"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["email"] == "owner@example.com"

    me = await unauthenticated_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"

    runs = await unauthenticated_client.get("/runs")
    assert runs.status_code == 200


async def test_logout_clears_the_session(unauthenticated_client: AsyncClient):
    await _create_user("owner@example.com", "correct horse battery staple")
    await unauthenticated_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "correct horse battery staple"}
    )
    assert (await unauthenticated_client.get("/auth/me")).status_code == 200

    logout = await unauthenticated_client.post("/auth/logout")
    assert logout.status_code == 200

    assert (await unauthenticated_client.get("/auth/me")).status_code == 401
    assert (await unauthenticated_client.get("/runs")).status_code == 401
