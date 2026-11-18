"""applyloop as an installed package: init, library API, legacy (pre-rename) paths."""
import os
from pathlib import Path

import applyloop


def test_public_api():
    from applyloop import Agent, AnswerBank, filter_reason, load_settings, route  # noqa: F401
    assert applyloop.__version__
    assert filter_reason({"title": "Data Engineer (TS/SCI)"}, {}).startswith("security clearance")


def test_init_project(tmp_path):
    from applyloop import init_project, load_settings, ProjectPaths
    done = dict(init_project(tmp_path))
    for f in ("config/applyloop.yml", "config/profile.yml", "config/answers.yml", "config/eeo.yml", ".env",
              "resumes/README.md"):
        assert (tmp_path / f).exists() and done[f] == "created"
    assert (tmp_path / "data").is_dir() and (tmp_path / ".gitignore").exists()
    assert oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"
    (tmp_path / "config" / "profile.yml").write_text("candidate:\n  full_name: Changed\n")
    assert dict(init_project(tmp_path))["config/profile.yml"].startswith("kept")      # never overwrites
    assert "Changed" in (tmp_path / "config" / "profile.yml").read_text()
    s = load_settings(ProjectPaths(tmp_path))
    assert [t.id for t in s.tracks] == ["data_engineering"]


def test_project_root_and_legacy_names(tmp_path, monkeypatch):
    from applyloop.config import find_project_root, import_legacy_env, ProjectPaths
    from applyloop.settings import load_settings, settings_path
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "jobpilot.yml").write_text("tracks: [{id: de, titles: [data engineer]}]\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "jobpilot.db").write_text("")
    sub = tmp_path / "output"
    sub.mkdir()
    monkeypatch.delenv("APPLYLOOP_HOME", raising=False)
    assert find_project_root(sub) == tmp_path
    paths = ProjectPaths(tmp_path)
    assert settings_path(paths).name == "jobpilot.yml"                 # old config still read
    assert load_settings(paths).db_path.name == "jobpilot.db"          # old database still used
    monkeypatch.setenv("APPLYLOOP_HOME", str(tmp_path))
    assert find_project_root(Path("/")) == tmp_path
    monkeypatch.setenv("JOBPILOT_IMAP_USER", "x@example.com")
    monkeypatch.delenv("APPLYLOOP_IMAP_USER", raising=False)
    import_legacy_env()
    assert os.environ["APPLYLOOP_IMAP_USER"] == "x@example.com"


def test_env_example_matches_template():
    from pathlib import Path
    from applyloop.config import STARTER_DIR
    root = Path(__file__).resolve().parents[1] / ".env.example"
    if root.exists():                       # source checkout: the repo copy must match what `init` writes
        assert root.read_text() == (STARTER_DIR / "env.example").read_text()
