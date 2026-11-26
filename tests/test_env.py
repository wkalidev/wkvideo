import os

from wkvideo.env import env_files, get_key, load_env, parse_env


def test_parse_env():
    text = '# c\nA=1\nexport B = "two words"\nC=\'x=y\'\nD=val # note\nE=\nbad line\n'
    assert parse_env(text) == {"A": "1", "B": "two words", "C": "x=y", "D": "val", "E": ""}


def test_load_env_fills_unset_and_empty_but_keeps_set_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("WKV_T1=file\nWKV_T2=file\nWKV_T3=file\n", encoding="utf-8")
    monkeypatch.setenv("WKV_T1", "shell")
    monkeypatch.setenv("WKV_T2", "")
    monkeypatch.setenv("WKV_T3", "")
    monkeypatch.delenv("WKV_T3")
    monkeypatch.chdir(tmp_path)
    try:
        assert load_env() == [tmp_path / ".env"]
        assert (get_key("WKV_T1"), get_key("WKV_T2"), get_key("WKV_T3")) == ("shell", "file", "file")
    finally:
        os.environ.pop("WKV_T3", None)


def test_env_files_search_parents_nearest_first(tmp_path):
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    (tmp_path / "a" / ".env").write_text("X=2", encoding="utf-8")
    found = env_files([child])
    assert found[:2] == [tmp_path / "a" / ".env", tmp_path / ".env"]


def test_explicit_env_file(tmp_path, monkeypatch):
    f = tmp_path / "keys.env"
    f.write_text("WKV_T4=from-file\n", encoding="utf-8")
    monkeypatch.setenv("WKV_T4", "")
    assert load_env(env_file=f) == [f]
    assert get_key("WKV_T4") == "from-file"
