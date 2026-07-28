import asyncio
import json

from textual.widgets import ListView

from codexier.model_client import LiveModel
from codexier.models import CodexSettings, ModelDefinition, Provider
from codexier.provider_store import ProviderStore, add_provider
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


def test_model_picker_keeps_every_saved_live_selection_without_a_cap():
    models = tuple(LiveModel(f"model-{index}", f"Model {index}") for index in range(6))
    screen = ModelPickerScreen(
        models,
        "Compatible API",
        selected_ids=tuple(model.id for model in models),
    )
    assert screen.selected == [model.id for model in models]


def test_multi_provider_copy_describes_enabled_provider_sync():
    copy = "\n".join(
        constant
        for compose in (
            ProviderManagerApp.compose,
            ApplyScreen.compose,
            ModelPickerScreen.compose,
        )
        for constant in compose.__code__.co_consts
        if isinstance(constant, str)
    )
    assert "Sync enabled providers" in copy
    assert "Codexier fallback" in copy
    assert "no selection limit" in copy


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
            assert not app.query("ProviderManagerApp Button")
            assert _visible_actions(app.screen) == {
                "add",
                "edit",
                "remove",
                "toggle_enabled",
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
            assert _visible_actions(app.screen) == {"toggle", "rename", "save", "cancel"}
            app.pop_screen()
            await pilot.pause()

            app.push_screen(SettingsScreen(catalog_path))
            await pilot.pause()
            assert _visible_actions(app.screen) == {"select", "cancel"}
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


def test_applied_provider_stays_default_regardless_of_highlight(tmp_path):
    async def scenario() -> None:
        fallback = Provider(
            "fallback", "Fallback", "https://fallback.example/v1", "secret",
            (ModelDefinition("model-a", "Model A"),), {},
        )
        other = Provider(
            "other", "Other", "https://other.example/v1", "secret",
            (ModelDefinition("model-b", "Model B"),), {},
        )
        catalog_path = tmp_path / "providers.json"
        catalog_path.write_text(json.dumps({"version": 1, "providers": []}))
        app = ProviderManagerApp(
            catalog_path,
            (other, fallback),
            tmp_path / "config.toml",
            applied_id="fallback",
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#providers", ListView).index = 0
            await pilot.pause()
            assert app._selected() == other
            app.action_use()
            await pilot.pause()
            assert isinstance(app.screen, ApplyScreen)
            assert app.screen.provider == fallback

    asyncio.run(scenario())


def test_enabling_earlier_provider_does_not_replace_existing_default(tmp_path):
    async def scenario() -> None:
        earlier = Provider(
            "earlier", "Earlier", "https://earlier.example/v1", "secret",
            (ModelDefinition("model-a", "Model A"),), {}, enabled=False,
        )
        default = Provider(
            "default", "Default", "https://default.example/v1", "secret",
            (ModelDefinition("model-b", "Model B"),), {},
        )
        catalog_path = tmp_path / "providers.json"
        catalog_path.write_text(json.dumps({"version": 1, "providers": []}))
        app = ProviderManagerApp(
            catalog_path,
            (earlier, default),
            tmp_path / "config.toml",
            applied_id="default",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#providers", ListView).index = 0
            app.action_toggle_enabled()
            await pilot.pause()
            app.action_use()
            await pilot.pause()

            assert isinstance(app.screen, ApplyScreen)
            assert app.screen.provider == default
            assert [provider.id for provider in app.screen.enabled_providers] == [
                "earlier",
                "default",
            ]

    asyncio.run(scenario())


def test_apply_persists_every_enabled_provider_with_existing_default(tmp_path):
    async def scenario() -> None:
        catalog_path = tmp_path / "providers.json"
        earlier = Provider(
            "earlier", "Earlier", "https://earlier.example/v1", "secret",
            (ModelDefinition("model-a", "Model A"),), {}, enabled=False,
        )
        default = Provider(
            "default", "Default", "https://default.example/v1", "secret",
            (ModelDefinition("model-b", "Model B"),), {},
        )
        add_provider(catalog_path, earlier)
        add_provider(catalog_path, default)
        app = ProviderManagerApp(
            catalog_path,
            (earlier, default),
            tmp_path / "config.toml",
            applied_id="default",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#providers", ListView).index = 0
            await pilot.pause()
            app.action_toggle_enabled()
            await pilot.pause()
            app.action_use()
            await pilot.pause()
            app.screen.action_apply_profile()
            await pilot.pause()

        saved = ProviderStore(catalog_path).load()
        assert [provider.id for provider in saved if provider.enabled] == [
            "earlier",
            "default",
        ]
        assert app.return_value == default

    asyncio.run(scenario())


def test_interactive_sync_starts_in_tui_instead_of_returning_to_cli(tmp_path, monkeypatch):
    async def scenario() -> None:
        provider = Provider(
            "demo", "Demo", "https://demo.example/v1", "secret",
            (ModelDefinition("model-a", "Model A"),), {},
        )
        catalog_path = tmp_path / "providers.json"
        add_provider(catalog_path, provider)
        app = ProviderManagerApp(
            catalog_path,
            (provider,),
            tmp_path / "config.toml",
            interactive_sync=True,
        )
        started: list[Provider] = []
        monkeypatch.setattr(app, "_begin_interactive_sync", started.append)

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_use()
            await pilot.pause()
            app.screen.action_apply_profile()
            await pilot.pause()

        assert started == [provider]
        assert app.return_value is None

    asyncio.run(scenario())
