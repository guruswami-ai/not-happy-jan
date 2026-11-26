"""Genre/language selection for the hold bed: folder tags, filtering, and the guarantees that
matter when a filter is wrong (a caller must still hear something)."""
from __future__ import annotations

import pytest

from nhj import inference_muzak as muzak


@pytest.fixture(autouse=True)
def pool(monkeypatch, tmp_path):
    """Look at exactly one music directory: no bundled audio, no inherited filters."""
    p = tmp_path / "music"
    p.mkdir()
    monkeypatch.setenv("NHJ_MUZAK_DIR", str(p))
    monkeypatch.delenv("NHJ_MUZAK_GENRES", raising=False)
    monkeypatch.delenv("NHJ_MUZAK_LANGUAGES", raising=False)
    monkeypatch.setattr(muzak, "_repo_root", lambda: tmp_path / "no-bundle")
    return p


def add(root, *parts):
    f = root.joinpath(*parts)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"fake-audio")
    return f


def names(paths):
    return sorted(p.name for p in paths)


def test_flat_library_is_unchanged(pool):
    add(pool, "one.m4a")
    add(pool, "two.mp3")
    add(pool, "notes.md")
    assert names(muzak.tracks()) == ["one.m4a", "two.mp3"]


def test_subfolders_are_found_now(pool):
    add(pool, "pub-rock", "cuda.m4a")
    add(pool, "bossa", "japanese", "hold.m4a")
    assert names(muzak.tracks()) == ["cuda.m4a", "hold.m4a"]


def test_genre_filter_is_comma_separated_and_case_insensitive(pool, monkeypatch):
    add(pool, "pub-rock", "cuda.m4a")
    add(pool, "bossa", "hold.m4a")
    add(pool, "blues", "context.m4a")
    monkeypatch.setenv("NHJ_MUZAK_GENRES", "Bossa-Nope, BOSSA , blues")
    assert names(muzak.tracks()) == ["context.m4a", "hold.m4a"]


def test_language_is_the_second_folder_level(pool, monkeypatch):
    add(pool, "bossa", "japanese", "hold-jp.m4a")
    add(pool, "bossa", "hold-en.m4a")          # one level deep: genre only, no language
    monkeypatch.setenv("NHJ_MUZAK_LANGUAGES", "japanese")
    assert names(muzak.tracks()) == ["hold-jp.m4a"]


def test_genre_and_language_combine(pool, monkeypatch):
    add(pool, "bossa", "japanese", "a.m4a")
    add(pool, "bossa", "english", "b.m4a")
    add(pool, "ska", "japanese", "c.m4a")
    monkeypatch.setenv("NHJ_MUZAK_GENRES", "bossa")
    monkeypatch.setenv("NHJ_MUZAK_LANGUAGES", "japanese")
    assert names(muzak.tracks()) == ["a.m4a"]


def test_untagged_tracks_do_not_match_a_genre(pool, monkeypatch):
    add(pool, "loose.m4a")                     # at the pool root: no tags at all
    add(pool, "bossa", "hold.m4a")
    monkeypatch.setenv("NHJ_MUZAK_GENRES", "bossa")
    assert names(muzak.tracks()) == ["hold.m4a"]


def test_a_filter_that_matches_nothing_never_silences_the_bed(pool, monkeypatch):
    add(pool, "bossa", "hold.m4a")
    add(pool, "unrated-loose.m4a")
    monkeypatch.setenv("NHJ_MUZAK_GENRES", "polka")
    assert names(muzak.tracks()) == ["hold.m4a", "unrated-loose.m4a"]


def test_dot_folders_and_dangling_symlinks_are_not_offered(pool):
    add(pool, ".staging", "draft.m4a")
    add(pool, "bossa", "hold.m4a")
    (pool / "bossa" / "gone.m4a").symlink_to(pool / "does-not-exist.m4a")
    assert names(muzak.tracks()) == ["hold.m4a"]


def test_the_same_file_via_two_directories_counts_once(tmp_path, monkeypatch):
    (tmp_path / "a" / "bossa").mkdir(parents=True)
    (tmp_path / "a" / "bossa" / "hold.m4a").write_bytes(b"x")
    (tmp_path / "b" / "bossa").mkdir(parents=True)
    (tmp_path / "b" / "bossa" / "hold-alias.m4a").symlink_to(tmp_path / "a" / "bossa" / "hold.m4a")
    monkeypatch.setenv("NHJ_MUZAK_DIR", f"{tmp_path / 'a'}:{tmp_path / 'b'}")
    monkeypatch.setattr(muzak, "_repo_root", lambda: tmp_path / "no-bundle")
    assert names(muzak.tracks()) == ["hold.m4a"]          # alias to the same file is dropped


def test_config_file_drives_the_filter_too(pool, monkeypatch):
    from nhj import config
    add(pool, "bossa", "hold.m4a")
    add(pool, "ska", "other.m4a")
    monkeypatch.setattr(config, "get", lambda: {"music": {"genres": ["ska"]}})
    assert names(muzak.tracks()) == ["other.m4a"]


def test_registry_documents_both_new_vars():
    from nhj import env_registry
    for name, yaml_key in (("NHJ_MUZAK_GENRES", "music.genres"),
                           ("NHJ_MUZAK_LANGUAGES", "music.languages")):
        e = env_registry.REGISTRY[name]
        assert (e.type, e.timing, e.yaml, e.description) == ("str", "startup", yaml_key, e.description)
