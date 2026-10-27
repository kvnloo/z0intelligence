from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from z0int import image_decision_cli as cli
from z0int.backends.image_decision import ImageDecisionOption, ImageDecisionResult


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def decide(
        self,
        *,
        image: Path,
        question: str,
        options: tuple[ImageDecisionOption, ...],
    ) -> ImageDecisionResult:
        self.calls.append(
            {"image": image, "question": question, "options": options}
        )
        return ImageDecisionResult(
            backend="fake",
            model="m",
            revision="r",
            choice=options[0].id,
            probabilities={options[0].id: 1.0},
            latency_ms=1.0,
            diagnostics={},
        )


def _sample_request(request_id: str = "ok") -> dict[str, object]:
    return {
        "id": request_id,
        "image": "i.png",
        "question": "q?",
        "options": [{"id": "a", "description": "A"}],
    }


def _batch_input() -> str:
    return (
        json.dumps(_sample_request("ok"))
        + "\n\n"
        + json.dumps({"id": "bad", "options": []})
        + "\n"
    )


def test_load_request_requires_object(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="request JSON must be an object"):
        cli._load_request(request)

    request.write_text('{"a": 1}', encoding="utf-8")
    assert cli._load_request(request) == {"a": 1}


def test_parse_options() -> None:
    parsed = cli._parse_options(
        [{"id": "a", "description": "A"}, {"id": 1, "description": "B"}]
    )
    assert isinstance(parsed, tuple)
    assert all(isinstance(option, ImageDecisionOption) for option in parsed)
    assert parsed == (
        ImageDecisionOption(id="a", description="A"),
        ImageDecisionOption(id="1", description="B"),
    )

    with pytest.raises(ValueError, match="options must be an array"):
        cli._parse_options("x")
    with pytest.raises(ValueError, match="each option must be an object"):
        cli._parse_options([1])
    with pytest.raises(ValueError, match="option id must be nonempty"):
        cli._parse_options([{"description": "d"}])
    with pytest.raises(ValueError, match="option description must be nonempty"):
        cli._parse_options([{"id": "a"}])


def test_resolve_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    absolute = tmp_path / "absolute.png"
    request = tmp_path / "sub" / "req.json"
    assert cli._resolve_image(request, str(absolute)) == absolute
    assert cli._resolve_image(request, "img.png") == (
        tmp_path / "sub" / "img.png"
    ).resolve()

    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli._resolve_image(request, "~/p.png") == tmp_path / "p.png"


def test_run_one_adds_schema_and_id(tmp_path: Path) -> None:
    fake = FakeBackend()
    raw = {
        "id": 7,
        "image": "i.png",
        "question": "q?",
        "options": [{"id": "a", "description": "A"}],
    }
    result = cli._run_one(fake, raw, tmp_path / "req.json")

    assert result["schema"] == "z0int.image_decision.v1"
    assert result["choice"] == "a"
    assert result["id"] == "7"
    assert fake.calls[0]["question"] == "q?"
    assert fake.calls[0]["image"] == (tmp_path / "i.png").resolve()
    assert fake.calls[0]["options"] == (
        ImageDecisionOption(id="a", description="A"),
    )

    without_id_or_question = dict(raw)
    without_id_or_question.pop("id")
    without_id_or_question.pop("question")
    result_without_id = cli._run_one(
        fake, without_id_or_question, tmp_path / "req.json"
    )
    assert "id" not in result_without_id
    assert fake.calls[-1]["question"] == ""


def test_run_one_errors(tmp_path: Path) -> None:
    fake = FakeBackend()
    source = tmp_path / "req.json"
    with pytest.raises(ValueError, match="request requires image"):
        cli._run_one(fake, {"options": []}, source)
    with pytest.raises(ValueError, match="options must be an array"):
        cli._run_one(fake, {"image": "i.png"}, source)
    assert fake.calls == []


def test_cmd_run_prints_sorted_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "req.json"
    source.write_text(json.dumps(_sample_request()), encoding="utf-8")
    fake = FakeBackend()
    monkeypatch.setattr(cli, "_backend", lambda args: fake)

    assert cli.cmd_run(Namespace(input=str(source))) == 0
    printed = capsys.readouterr().out
    obj = json.loads(printed)
    assert obj["choice"] == "a"
    assert printed == json.dumps(obj, indent=2, sort_keys=True) + "\n"
    assert len(fake.calls) == 1


def test_cmd_batch_error_rows_and_blank_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "requests.jsonl"
    source.write_text(_batch_input(), encoding="utf-8")
    fake = FakeBackend()
    monkeypatch.setattr(cli, "_backend", lambda args: fake)

    assert cli.cmd_batch(Namespace(input=str(source), output=None)) == 0
    printed = capsys.readouterr().out
    rows = [json.loads(line) for line in printed.splitlines()]
    assert len(rows) == 2
    assert rows[0]["schema"] == "z0int.image_decision.v1"
    assert rows[0]["choice"] == "a"
    assert rows[0]["id"] == "ok"
    assert rows[1] == {
        "schema": "z0int.image_decision_error.v1",
        "id": "bad",
        "error": "ValueError: request requires image",
    }
    assert printed.endswith("\n")
    assert len(fake.calls) == 1


def test_cmd_batch_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "requests.jsonl"
    source.write_text(_batch_input(), encoding="utf-8")
    target = tmp_path / "deep" / "out.jsonl"
    monkeypatch.setattr(cli, "_backend", lambda args: FakeBackend())

    assert cli.cmd_batch(Namespace(input=str(source), output=str(target))) == 0
    assert capsys.readouterr().out == ""
    assert target.parent.is_dir()

    expected_rows = [
        {
            "schema": "z0int.image_decision.v1",
            "backend": "fake",
            "model": "m",
            "revision": "r",
            "choice": "a",
            "probabilities": {"a": 1.0},
            "latency_ms": 1.0,
            "diagnostics": {},
            "id": "ok",
        },
        {
            "schema": "z0int.image_decision_error.v1",
            "id": "bad",
            "error": "ValueError: request requires image",
        },
    ]
    expected = "\n".join(json.dumps(row, sort_keys=True) for row in expected_rows) + "\n"
    assert target.read_text(encoding="utf-8") == expected

    source.write_text("", encoding="utf-8")
    assert cli.cmd_batch(Namespace(input=str(source), output=str(target))) == 0
    assert target.read_text(encoding="utf-8") == ""
    assert capsys.readouterr().out == ""


def test_cmd_batch_malformed_rows_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "requests.jsonl"
    monkeypatch.setattr(cli, "_backend", lambda args: FakeBackend())

    source.write_text("\n[1]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2: row must be an object"):
        cli.cmd_batch(Namespace(input=str(source), output=None))

    source.write_text("{bad\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        cli.cmd_batch(Namespace(input=str(source), output=None))


def test_build_parser_defaults_and_errors() -> None:
    parser = cli.build_parser()
    run = parser.parse_args(["run", "--input", "x"])
    assert run.model == cli.DEFAULT_MODEL_ID
    assert run.revision == cli.DEFAULT_MODEL_REVISION
    assert run.device is None
    assert run.dtype == "bfloat16"
    assert run.func is cli.cmd_run

    batch = parser.parse_args(["batch", "--input", "x"])
    assert batch.output is None
    assert batch.func is cli.cmd_batch

    for argv in ([], ["run", "--input", "x", "--dtype", "int8"]):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 2


def test_backend_builds_from_args(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict[str, object]] = []

    class RecorderBackend:
        def __init__(self, **kwargs: object) -> None:
            received.append(kwargs)

    monkeypatch.setattr(cli, "QwenImageDecisionBackend", RecorderBackend)
    instance = cli._backend(
        Namespace(model="m", revision="r", device="cpu", dtype="fp16")
    )

    assert isinstance(instance, RecorderBackend)
    assert received == [
        {
            "model_id": "m",
            "revision": "r",
            "device": "cpu",
            "dtype": "fp16",
        }
    ]


def test_main_returns_func_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "req.json"
    source.write_text(json.dumps(_sample_request()), encoding="utf-8")
    fake = FakeBackend()
    monkeypatch.setattr(cli, "_backend", lambda args: fake)
    monkeypatch.setattr(
        sys, "argv", ["z0int-image-decide", "run", "--input", str(source)]
    )

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["choice"] == "a"
    assert len(fake.calls) == 1
