import pytest

from codexier.model_client import ModelFetchError, fetch_models, parse_models_response


def test_parse_openai_models_response_sorts_and_deduplicates_ids():
    result = parse_models_response({"data": [{"id": "zeta"}, {"id": "alpha"}, {"id": "zeta"}]})
    assert [model.id for model in result] == ["alpha", "zeta"]


def test_parse_models_response_rejects_invalid_payload():
    with pytest.raises(ModelFetchError, match="data"):
        parse_models_response({"models": []})


def test_parse_models_response_ignores_blank_ids():
    with pytest.raises(ModelFetchError, match="valid models"):
        parse_models_response({"data": [{"id": "  "}]})


def test_fetch_models_calls_openai_models_endpoint_with_bearer_key():
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "live-model"}]}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    models = fetch_models("https://provider.example/v1", "secret", get=get)
    assert [model.id for model in models] == ["live-model"]
    assert calls == [("https://provider.example/v1/models", {
        "headers": {"Authorization": "Bearer secret", "Accept": "application/json"},
        "timeout": 10.0,
    })]


def test_fetch_models_adds_v1_when_base_url_has_no_api_prefix():
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "live-model"}]}

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    fetch_models("https://provider.example", "secret", get=get)
    assert calls == ["https://provider.example/v1/models"]
