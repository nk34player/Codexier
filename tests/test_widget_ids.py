from codexier.tui import widget_id


def test_widget_id_accepts_common_model_names():
    assert widget_id("model", "gpt-5.2") == "model-gpt-5_u2e_2"
    assert widget_id("model", "claude/opus 4.8") == "model-claude_u2f_opus_u20_4_u2e_8"


def test_widget_id_never_starts_with_number_or_collides_on_punctuation():
    assert widget_id("model", "123") == "model-x-123"
    assert widget_id("model", "a.b") != widget_id("model", "a-b")
