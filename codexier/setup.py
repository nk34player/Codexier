from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from .errors import ValidationError
from .model_client import LiveModel
from .models import ModelDefinition, Provider, validate_base_url


def normalize_base_url(base_url: str) -> str:
    validate_base_url(base_url)
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        normalized_path = path
    elif path in {"", "/"}:
        normalized_path = "/v1"
    else:
        normalized_path = f"{path}/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path + "/", "", ""))


def provider_from_form(name: str, base_url: str, api_key: str, model_ids: Sequence[str]) -> Provider:
    name = name.strip()
    api_key = api_key.strip()
    if not name:
        raise ValidationError("Provider name is required.")
    if not api_key:
        raise ValidationError("API key is required.")
    base_url = normalize_base_url(base_url)
    if not model_ids:
        raise ValidationError("At least one model is required.")
    provider_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    models = tuple(ModelDefinition(model_id, model_id) for model_id in model_ids)
    return Provider(provider_id, name.strip(), base_url.strip(), api_key, models, {}, False)


def provider_from_live_models(name: str, base_url: str, api_key: str, models: Sequence[LiveModel]) -> Provider:
    provider = provider_from_form(name, base_url, api_key, [model.id for model in models])
    return Provider(
        provider.id,
        provider.name,
        provider.base_url,
        provider.api_key,
        tuple(ModelDefinition(model.id, model.label) for model in models),
        {},
        False,
    )
