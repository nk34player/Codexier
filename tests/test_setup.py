import pytest

from codexier.errors import ValidationError
from codexier.setup import normalize_base_url, provider_from_form


def test_provider_from_form_normalizes_id_and_models():
    provider = provider_from_form(
        "My Provider", "https://example.com/v1", "secret", ["model-z", "model-a"]
    )
    assert provider.id == "my-provider"
    assert [model.id for model in provider.models] == ["model-z", "model-a"]


@pytest.mark.parametrize("url", ["https://example.com", "https://example.com/", "https://example.com/v1/"])
def test_normalize_base_url_ends_with_v1_without_trailing_slash(url):
    assert normalize_base_url(url) == "https://example.com/v1"


def test_provider_form_rejects_missing_required_values():
    with pytest.raises(ValidationError):
        provider_from_form("", "https://example.com/v1", "key", ["model"])
    with pytest.raises(ValidationError):
        provider_from_form("Demo", "http://example.com/v1", "key", ["model"])
    with pytest.raises(ValidationError):
        provider_from_form("Demo", "https://example.com/v1", "", ["model"])
