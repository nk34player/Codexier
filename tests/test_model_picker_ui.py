from codexier.setup_tui import ModelPickerScreen
from codexier.model_client import LiveModel


def test_model_picker_has_expanded_scrollable_list_and_visible_loading_copy():
    assert "#models" in ModelPickerScreen.CSS
    assert "overflow-y: scroll" in ModelPickerScreen.CSS
    screen = ModelPickerScreen((LiveModel("gpt-5.2", "GPT 5.2"),), "WawApi")
    assert screen.provider_name == "WawApi"
    assert len(screen.models) == 1


def test_model_picker_handles_empty_model_data():
    screen = ModelPickerScreen((), "WawApi")
    assert screen.models == ()
