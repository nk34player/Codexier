import asyncio
import json

from codexier.model_client import LiveModel
from codexier.models import CodexSettings, ModelDefinition, Provider
from codexier.provider_store import ProviderStore
from codexier.setup_tui import (
    ApplyScreen,
    ModelPickerScreen,
    ProviderFormScreen,
    ProviderManagerApp,
    SettingsScreen,
)


def test_model_picker_has_expanded_scrollable_list_and_visible_loading_copy():
    assert "#models" in ModelPickerScreen.CSS
    assert "overflow-y: scroll" in ModelPickerScreen.CSS
    screen = ModelPickerScreen((LiveModel("gpt-5.2", "GPT 5.2"),), "WawApi")
    assert screen.provider_name == "WawApi"
    assert len(screen.models) == 1


def test_model_picker_handles_empty_model_data():
    screen = ModelPickerScreen((), "WawApi")
    assert screen.models == ()


def test_model_picker_keeps_saved_live_selection():
    screen = ModelPickerScreen(
        (LiveModel("model-a", "Model A"), LiveModel("model-b", "Model B")),
        "WawApi",
        selected_ids=("model-b", "removed-model"),
    )
    assert screen.selected == ["model-b"]
    assert "●" in screen._label("model-b")


def _visible_actions(screen) -> set[str]:
    return {
        binding.action
        for _, binding, _, _ in screen.active_bindings.values()
        if binding.show
    }


def test_each_page_exposes_only_its_relevant_footer_actions(tmp_path):
    async def scenario() -> None:
        provider = Provider(
            "demo",
            "Demo",
            "https://demo.example/v1",
            "secret",
            (ModelDefinition("model-a", "Model A"),),
            {},
        )
        catalog_path = tmp_path / "providers.json"
        catalog_path.write_text(json.dumps({"version": 1, "providers": []}))
        app = ProviderManagerApp(catalog_path, (provider,), tmp_path / "config.toml")

        async with app.run_test() as pilot:
            await pilot.pause()
            assert _visible_actions(app.screen) == {
                "add",
                "edit",
                "remove",
                "select_cursor",
                "quit",
                "settings",
            }

            app.push_screen(ProviderFormScreen(catalog_path, None))
            await pilot.pause()
            assert _visible_actions(app.screen) == {"submit", "cancel"}
            app.pop_screen()
            await pilot.pause()

            app.push_screen(ModelPickerScreen((LiveModel("model-a", "Model A"),), "Demo"))
            await pilot.pause()
            assert _visible_actions(app.screen) == {"toggle", "save", "cancel"}
            app.pop_screen()
            await pilot.pause()

            app.push_screen(SettingsScreen(catalog_path))
            await pilot.pause()
            assert _visible_actions(app.screen) == {"toggle", "save", "cancel"}
            app.pop_screen()
            await pilot.pause()

            app.push_screen(
                ApplyScreen(
                    provider,
                    CodexSettings(provider.base_url, provider.api_key, ("model-a",)),
                    tmp_path / "config.toml",
                )
            )
            await pilot.pause()
            assert _visible_actions(app.screen) == {
                "activate",
                "cancel",
                "focus_previous_button",
                "focus_next_button",
            }

    asyncio.run(scenario())


def test_enter_saves_provider_after_model_selection(tmp_path):
    async def scenario() -> None:
        catalog_path = tmp_path / "providers.json"
        catalog_path.write_text(json.dumps({"version": 1, "providers": []}))
        models = (
            LiveModel("model-a", "Model A"),
            LiveModel("model-b", "Model B"),
        )
        app = ProviderManagerApp(catalog_path, ())

        async with app.run_test() as pilot:
            form = ProviderFormScreen(catalog_path, None)
            app.push_screen(form)
            await pilot.pause()
            app.push_screen(
                ModelPickerScreen(models, "Demo"),
                lambda selected: form._save_selected(
                    "Demo",
                    "https://demo.example/v1/",
                    "secret",
                    models,
                    selected,
                ),
            )
            await pilot.pause()
            await pilot.press("space")
            await pilot.press("enter")
            await pilot.pause()

            saved = ProviderStore(catalog_path).get("demo")
            assert tuple(model.id for model in saved.models) == ("model-a",)

    asyncio.run(scenario())
