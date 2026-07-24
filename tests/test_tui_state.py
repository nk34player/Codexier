import pytest

from codexier.tui import SelectionState


def test_selection_state_toggles_and_caps_at_five():
    state = SelectionState(("a", "b", "c", "d", "e", "f"))
    for model in ("a", "b", "c", "d", "e"):
        state.toggle(model)
    with pytest.raises(ValueError, match="5"):
        state.toggle("f")
    assert state.selected == ("a", "b", "c", "d", "e")


def test_selection_state_hydrates_existing_selection():
    state = SelectionState(("a", "b", "c"), initial=("b", "missing"))
    assert state.selected == ("b",)


def test_selection_state_requires_one_model():
    state = SelectionState(("a",))
    with pytest.raises(ValueError, match="one"):
        state.confirm()
    state.toggle("a")
    assert state.confirm() == ("a",)
