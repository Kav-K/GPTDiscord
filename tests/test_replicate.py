"""Replicate SDK boundaries are simulated; hosted model availability is unverified."""

from unittest.mock import Mock

import pytest

pytest.importorskip("replicate")

from models.image_understanding_model import ImageUnderstandingModel


@pytest.mark.parametrize(
    "method,prefix,fields",
    [
        ("ask_image_question", "andreasjansson/blip-2:", {"question": "Describe this"}),
        (
            "get_llava_answer",
            "yorickvp/llava-13b:",
            {"prompt": "Describe this", "max_tokens": 1024},
        ),
        ("get_minigpt_answer", "daanelson/minigpt-4:", {"prompt": "Describe this"}),
        ("get_image_caption", "andreasjansson/blip-2:", {"caption": True}),
        ("get_image_stylistic_caption", "pharmapsychotic/clip-interrogator:", {}),
    ],
)
def test_fallback_models_send_binary_files_and_keep_sdk_text_outputs(
    monkeypatch, tmp_path, method, prefix, fields
):
    import replicate

    monkeypatch.setenv("REPLICATE_API_KEY", "offline-provider-key")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "offline-original-token")
    path = tmp_path / "fixture.png"
    path.write_bytes(b"offline image fixture")
    inputs = []

    def run(model, *, input):
        assert model.startswith(prefix)
        assert input["image"].read() == b"offline image fixture"
        assert input["image"].mode == "rb"
        inputs.append(input)
        for name, value in fields.items():
            assert input[name] == value
        return (
            iter(["A readable ", "image description."])
            if method == "get_llava_answer"
            else "A readable image description."
        )

    spy = Mock(side_effect=run)
    monkeypatch.setattr(replicate, "run", spy)
    model = ImageUnderstandingModel()
    assert model.get_is_usable()
    args = (
        ("Describe this", str(path))
        if method in {"ask_image_question", "get_llava_answer", "get_minigpt_answer"}
        else (str(path),)
    )

    output = getattr(model, method)(*args)

    assert (
        output if isinstance(output, str) else "".join(output)
    ) == "A readable image description."
    assert inputs[0]["image"].closed
    spy.assert_called_once()


def test_provider_failure_closes_uploaded_file(monkeypatch, tmp_path):
    import replicate

    path = tmp_path / "fixture.png"
    path.write_bytes(b"offline image fixture")
    files = []

    def fail(model, *, input):
        files.append(input["image"])
        raise RuntimeError("Provider unavailable")

    monkeypatch.setattr(replicate, "run", fail)
    with pytest.raises(RuntimeError, match="Provider unavailable"):
        ImageUnderstandingModel._run("provider/model:version", str(path))
    assert files[0].closed
