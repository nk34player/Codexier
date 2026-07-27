import pytest

from codexier.desktop_patch_macos import PatchError, validate_provider_config


def test_provider_first_desktop_config_accepts_duplicate_model_ids_across_providers():
    validate_provider_config(
        {
            "version": 2,
            "default_provider": "codexier-one",
            "providers": [
                {
                    "id": "openai",
                    "label": "OpenAI",
                    "description": "",
                    "models": [],
                },
                {
                    "id": "codexier-one",
                    "label": "One",
                    "description": "",
                    "models": [{"id": "shared", "label": "Shared One"}],
                },
                {
                    "id": "codexier-two",
                    "label": "Two",
                    "description": "",
                    "models": [{"id": "shared", "label": "Shared Two"}],
                },
            ],
        }
    )


def test_provider_first_desktop_config_rejects_duplicate_models_within_provider():
    with pytest.raises(PatchError, match="duplicate model id"):
        validate_provider_config(
            {
                "version": 2,
                "default_provider": "one",
                "providers": [
                    {
                        "id": "one",
                        "label": "One",
                        "models": [
                            {"id": "same", "label": "Same"},
                            {"id": "same", "label": "Again"},
                        ],
                    }
                ],
            }
        )
