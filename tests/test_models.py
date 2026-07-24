import pytest

from codexier.errors import ValidationError
from codexier.models import (
    CodexSettings,
    ModelDefinition,
    Provider,
    mask_api_key,
    select_models,
    validate_provider,
    validate_settings,
)


def provider(**changes):
    values = dict(
        id="demo",
        name="Demo",
        base_url="https://example.com/v1",
        api_key="sk-secret-value",
        models=(ModelDefinition("one", "One"), ModelDefinition("two", "Two")),
        presets={"default": ("one", "two")},
    )
    values.update(changes)
    return Provider(**values)


def test_valid_provider():
    validate_provider(provider())


@pytest.mark.parametrize("url", ["http://example.com/v1", "example.com", "https://u:p@example.com/v1"])
def test_invalid_base_url(url):
    with pytest.raises(ValidationError):
        validate_provider(provider(base_url=url))


def test_empty_key_rejected():
    with pytest.raises(ValidationError):
        validate_provider(provider(api_key=""))


def test_selection_enforces_one_to_five_and_known_ids():
    with pytest.raises(ValidationError):
        select_models(provider(), [])
    with pytest.raises(ValidationError):
        select_models(provider(), ["one"] * 6)
    with pytest.raises(ValidationError):
        select_models(provider(), ["missing"])
    assert select_models(provider(), ["two", "one"]) == ("two", "one")


def test_settings_enforces_unique_models():
    with pytest.raises(ValidationError):
        validate_settings(CodexSettings("https://example.com", "key", ("one", "one")))


def test_masks_key():
    assert mask_api_key("short") == "*****"
    assert mask_api_key("sk-secret-value") == "sk-...alue"
