"""Streamlit checks for the Care Advisor tab."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def _widget(items, label):
    for item in items:
        if item.label == label:
            return item
    raise AssertionError(f"No widget labeled {label}")


def test_advisor_tab_proposes_accepts_and_refuses_a_dose():
    app = AppTest.from_file(str(APP))
    app.run()

    _widget(app.text_input, "Pet name").set_value("Mochi")
    _widget(app.text_input, "Breed (optional)").set_value("Golden Retriever")
    _widget(app.number_input, "Age (years)").set_value(3)
    _widget(app.button, "Add Pet").click()
    app.run()

    _widget(app.button, "Generate today's care plan").click()
    app.run()
    combined = "\n".join(item.value for item in app.markdown)
    assert "Morning walk" in combined
    assert "dog-exercise-adult" in combined

    _widget(app.button, "Accept and add to schedule").click()
    app.run()
    after_accept = "\n".join(item.value for item in app.markdown)
    assert "Morning walk" in after_accept
    assert any("Added Morning walk" in item.value for item in app.success)

    _widget(app.text_input, "Question").set_value(
        "How many mg of ibuprofen can I give Mochi?"
    )
    _widget(app.selectbox, "Answer for").set_value("Mochi")
    _widget(app.button, "Ask").click()
    app.run()
    assert any("medication dose" in item.value for item in app.error)
