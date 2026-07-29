from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import httpx
from urllib.parse import urlsplit, urlunsplit

from .errors import ValidationError
from .models import validate_base_url


class ModelFetchError(Exception):
    """Live provider model discovery failed."""


@dataclass(frozen=True)
class LiveModel:
    id: str
    label: str


def _models_endpoint(base_url: str) -> str:
    try:
        validate_base_url(base_url)
    except ValidationError as exc:
        raise ModelFetchError(str(exc)) from exc
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    elif not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/", "", "")) + "models"


def parse_models_response(payload: Any) -> tuple[LiveModel, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ModelFetchError("Provider response must contain a data array.")
    models: dict[str, LiveModel] = {}
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        model_id = item["id"].strip()
        if model_id:
            label = item.get("name") if isinstance(item.get("name"), str) else model_id
            models[model_id] = LiveModel(model_id, label.strip() or model_id)
    if not models:
        raise ModelFetchError("Provider returned no valid models.")
    return tuple(models[key] for key in sorted(models, key=str.casefold))


def fetch_models(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 10.0,
    get: Callable[..., Any] | None = None,
) -> tuple[LiveModel, ...]:
    endpoint = _models_endpoint(base_url)
    requester = get or httpx.get
    try:
        response = requester(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise ModelFetchError("Could not fetch live models from provider /v1/models.") from exc
    return parse_models_response(payload)
