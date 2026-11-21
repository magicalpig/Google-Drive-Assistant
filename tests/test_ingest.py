import ingest
from ingest import CHUNK_SIZE, clean, full_name, ignored, split_text


def test_split_text_keeps_paragraphs_together():
    paragraphs = [f"Paragraph {i} " + "word " * 50 for i in range(20)]
    chunks = split_text("\n".join(paragraphs))
    assert len(chunks) > 1
    # Every paragraph ends up whole in exactly one chunk.
    for p in paragraphs:
        assert sum(p.strip() in c for c in chunks) == 1


def test_split_text_cuts_a_very_long_paragraph():
    chunks = split_text("x" * (CHUNK_SIZE * 2 + 10))
    assert all(len(c.strip()) <= CHUNK_SIZE for c in chunks)
    assert "".join(c.strip() for c in chunks) == "x" * (CHUNK_SIZE * 2 + 10)


def test_split_text_skips_blank_lines():
    assert split_text("\n\n  \n") == []


def test_full_name():
    assert full_name({"folder": "Tax/Tax 2025", "name": "W2.pdf"}) == "Tax/Tax 2025/W2.pdf"
    assert full_name({"folder": "", "name": "Notes"}) == "Notes"


def test_clean_replaces_broken_characters():
    assert clean("ok \ud800 ok") == "ok ? ok"


def test_ignored_folders_and_their_subfolders(monkeypatch):
    monkeypatch.setattr(ingest, "IGNORE_FOLDERS", ["Work/Archive", "Old"])
    assert ignored({"folder": "Work/Archive"})
    assert ignored({"folder": "Work/Archive/2020"})
    assert ignored({"folder": "Old"})
    assert not ignored({"folder": "Work/Archived"})  # a different folder whose name starts the same
    assert not ignored({"folder": "Older"})
    assert not ignored({"folder": ""})
