"""Authenticated API executor.

The model asks for ``GET /orders as CUSTOMER``. This module attaches the token
and returns only status + sanitised body. The ``Authorization`` header never
crosses back.
"""

from __future__ import annotations

from typing import Any

import httpx

from qa_copilot.config import Config, Environment
from qa_copilot.identity.broker import IdentityBroker, IdentityError
from qa_copilot.sanitize import sanitizer
from qa_copilot.secrets.base import SecretValue

_MAX_BODY_CHARS = 8_000


class ApiExecutor:
    def __init__(self, config: Config, broker: IdentityBroker) -> None:
        self.config = config
        self.broker = broker
        self._token_cache: dict[tuple[str, str], SecretValue] = {}

    async def _token(self, env: Environment, identity_alias: str) -> SecretValue | None:
        key = (env.name, identity_alias)
        if key in self._token_cache:
            return self._token_cache[key]

        identity = self.config.identity(identity_alias)
        token = self.broker.api_token(identity)
        if token is None and env.api_auth.login_path:
            creds = self.broker.credentials(identity)
            base = (env.api_base_url or env.base_url).rstrip("/")
            async with httpx.AsyncClient(verify=env.verify_tls, timeout=20.0) as client:
                resp = await client.post(
                    base + env.api_auth.login_path,
                    json={"username": creds.username.reveal(), "password": creds.password.reveal()},
                )
            if resp.status_code >= 400:
                raise IdentityError(
                    f"API login for {identity_alias!r} failed with status {resp.status_code}"
                )
            payload = resp.json()
            raw = payload.get("token") or payload.get("access_token")
            if not raw:
                raise IdentityError(
                    f"API login for {identity_alias!r} returned no token field"
                )
            token = SecretValue(alias=f"runtime://{env.name}/{identity_alias}/token", _value=raw)

        if token is not None:
            self._token_cache[key] = token
        return token

    async def request(
        self,
        *,
        environment: str,
        method: str,
        path: str,
        identity: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        env = self.config.environment(environment)
        base = (env.api_base_url or env.base_url).rstrip("/")
        url = path if path.startswith("http") else base + "/" + path.lstrip("/")

        headers: dict[str, str] = {"Accept": "application/json"}
        if identity:
            token = await self._token(env, identity)
            if token is not None:
                auth = env.api_auth
                if auth.mode == "bearer":
                    headers[auth.header_name] = f"Bearer {token.reveal()}"
                elif auth.mode == "header":
                    headers[auth.header_name] = token.reveal()

        async with httpx.AsyncClient(
            verify=env.verify_tls, timeout=30.0, follow_redirects=False
        ) as client:
            resp = await client.request(method, url, json=json_body, headers=headers)

        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text[:_MAX_BODY_CHARS]

        safe_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in {"set-cookie", "authorization", "www-authenticate"}
        }
        return sanitizer.scrub(
            {
                "status": resp.status_code,
                "url": str(resp.url),
                "identity": identity,
                "headers": safe_headers,
                "body": body,
                "secret_values_exposed": False,
            }
        )
