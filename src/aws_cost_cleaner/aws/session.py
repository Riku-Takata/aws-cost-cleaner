"""AWS session management and guarded client factory."""

from __future__ import annotations

from typing import Any

import boto3  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.guard import Mode, install_guard


class ClientFactory:
    """Factory for creating and caching guarded boto3 clients."""

    def __init__(self, mode: Mode, profile: str | None = None) -> None:
        self._mode: Mode = mode
        self._session: Any = boto3.Session(profile_name=profile)
        self._clients: dict[tuple[str, str], Any] = {}

    def client(self, service: str, region: str) -> Any:
        """Return a cached or newly created guarded client for service and region."""
        key = (service, region)
        if key in self._clients:
            return self._clients[key]
        c = self._session.client(service, region_name=region)
        install_guard(c, self._mode)
        self._clients[key] = c
        return c
