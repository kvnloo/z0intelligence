"""Bounded historical views for the native OMP loop. No model or host state."""
import pytest

from z0int.optchat.__main__ import Resident
from z0int.optchat.compact import Scheduler, pump
from z0int.optchat.log import ChatLog


def test_view_uses_built_nodes_and_excludes_live_messages(tmp_path):
    chat = ChatLog(tmp_path)
    for text in ("prior user", "prior assistant", "current user", "live tool result"):
        chat.append("user", text)
    pump(chat, lambda _: "unused")
    view = chat.render_ids([0, 1])
    assert view == "<chat>\n0+2|user: prior user user: prior assistant\n</chat>"
    assert "current" not in view and "live" not in view
    chat.append("talk", "later result")
    assert chat.render_ids([0, 1]) == view


def test_missing_summary_keeps_history_instead_of_zoom_prompt(tmp_path):
    chat = ChatLog(tmp_path)
    chat.append("talk", "x" * 900)
    assert chat.render_ids([0]) is None
    pump(chat, lambda _: "earlier reply summary")
    assert chat.render_ids([0]) == "<chat>\n0+1|earlier reply summary\n</chat>"


def test_noncontiguous_ids_do_not_include_other_sessions(tmp_path):
    chat = ChatLog(tmp_path)
    for text in ("mine", "other session", "mine again"):
        chat.append("user", text)
    pump(chat, lambda _: "unused")
    assert chat.render_ids([0, 2]) == "<chat>\n0+1|user: mine\n2+1|user: mine again\n</chat>"


@pytest.mark.parametrize("ids", [[True], [-1], [0.5], ["0"], [1], [0, 0], [1, 0]])
def test_invalid_ids_fail_closed(tmp_path, ids):
    chat = ChatLog(tmp_path)
    chat.append("user", "one")
    with pytest.raises(ValueError):
        chat.render_ids(ids)


def test_resident_view_ids_and_off_gate(tmp_path):
    chat = ChatLog(tmp_path)
    chat.append("user", "prior prompt")
    scheduler = Scheduler(chat, lambda _: "unused")
    resident = Resident(chat, lambda _: None, scheduler)
    assert resident.dispatch({"op": "view_ids", "ids": [0]}) == {
        "ok": True, "ready": True, "view": "<chat>\n0+1|user: prior prompt\n</chat>"}
    with pytest.raises(ValueError):
        resident.dispatch({"op": "view_ids", "ids": "0"})
    scheduler.set_enabled(False)
    assert resident.dispatch({"op": "view_ids", "ids": [0]}) == {"ok": False, "error": "optchat_off"}


def test_flag_path_matches_harness_override(monkeypatch, tmp_path):
    from z0int.optchat.switch import enabled, flag_path, set_enabled
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("Z0INT_OPTCHAT_ROOT", raising=False)
    assert flag_path() == tmp_path / "home/optchat/enabled"
    assert not enabled()
    monkeypatch.setenv("Z0INT_OPTCHAT_ROOT", str(tmp_path / "harness/chat"))
    assert flag_path() == tmp_path / "harness/enabled"
    set_enabled(True)
    assert enabled()
    set_enabled(False)
    assert not enabled()
