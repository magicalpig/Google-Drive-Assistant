from datetime import date
from types import SimpleNamespace

import chat
from chat import CANDIDATES_PER_YEAR_QUESTION, PASSAGES_PER_QUESTION, find_passages, question_years, year_rank


class FakeModel:
    def encode(self, text, normalize_embeddings):
        return SimpleNamespace(tolist=lambda: [0.0])


class FakeIndex:
    """Stands in for the Pinecone index. Returns passages from these files, best match first."""
    def __init__(self, file_names):
        self.file_names = file_names
        self.top_k = None

    def query(self, vector, top_k, include_metadata):
        self.top_k = top_k
        matches = [SimpleNamespace(metadata={"file_name": n}) for n in self.file_names[:top_k]]
        return SimpleNamespace(matches=matches)


FILES = ["Tax/Tax 2024/W2.pdf", "Budget", "Tax/Tax 2025/W2.pdf", "Car/Service 2019/Oil.pdf", "Tax/Notes"]


def names(passages):
    return [p["file_name"] for p in passages]


def test_question_years():
    today = date(2026, 11, 21)
    assert question_years("What was my 2025 refund?", today) == {"2025"}
    assert question_years("HSA left for the current year?", today) == {"2026"}
    assert question_years("What did I pay last year?", today) == {"2025"}
    assert question_years("Compare this year's pay to Next Year", today) == {"2026", "2027"}
    assert question_years("What was my refund?", today) == set()
    assert question_years("What did I pay in the yearly plan?", today) == set()


def test_year_rank():
    years = {"2025"}
    assert year_rank("Tax/Tax 2025/W2.pdf", years) == 0
    assert year_rank("Tax/W2 2025.pdf", years) == 0  # the year can be in the file name
    assert year_rank("Budget", years) == 1
    assert year_rank("Tax/Tax 2024/W2.pdf", years) == 2
    assert year_rank("Archive 2020/Tax 2025/W2.pdf", years) == 0  # any matching year counts


def test_question_without_a_year_keeps_pinecone_order(monkeypatch):
    monkeypatch.setattr(chat, "QUESTION_PREFIX", "")
    index = FakeIndex(FILES)
    assert names(find_passages(index, FakeModel(), "what was my refund?")) == FILES
    assert index.top_k == PASSAGES_PER_QUESTION


def test_question_with_a_year_moves_that_year_up(monkeypatch):
    monkeypatch.setattr(chat, "STRICT_YEAR_FOLDERS", [])
    index = FakeIndex(FILES)
    assert names(find_passages(index, FakeModel(), "what was my 2025 refund?")) == [
        "Tax/Tax 2025/W2.pdf", "Budget", "Tax/Notes", "Tax/Tax 2024/W2.pdf", "Car/Service 2019/Oil.pdf"]
    assert index.top_k == CANDIDATES_PER_YEAR_QUESTION


def test_strict_folder_leaves_out_other_years(monkeypatch):
    monkeypatch.setattr(chat, "STRICT_YEAR_FOLDERS", ["Tax"])
    result = names(find_passages(FakeIndex(FILES), FakeModel(), "what was my 2025 refund?"))
    assert "Tax/Tax 2024/W2.pdf" not in result
    assert "Car/Service 2019/Oil.pdf" in result  # not in a strict folder, so only moved down
    assert "Tax/Notes" in result  # no year in its path, so kept


def test_only_the_top_passages_are_kept(monkeypatch):
    many = [f"File {i}" for i in range(50)]
    assert len(find_passages(FakeIndex(many), FakeModel(), "question 2025")) == PASSAGES_PER_QUESTION
