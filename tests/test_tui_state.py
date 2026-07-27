import pytest

from codexier.tui import SelectionState


def test_selection_state_toggles_without_a_model_cap():
    state = SelectionState(("a", "b", "c", "d", "e", "f"))
    for model in ("a", "b", "c", "d", "e", "f"):
        state.toggle(model)
    assert state.selected == ("a", "b", "c", "d", "e", "f")


def test_selection_state_hydrates_existing_selection():
    state = SelectionState(("a", "b", "c"), initial=("b", "missing"))
    assert state.selected == ("b",)


def test_selection_state_requires_one_model():
    state = SelectionState(("a",))
    with pytest.raises(ValueError, match="one"):
        state.confirm()
    state.toggle("a")
    assert state.confirm() == ("a",)
