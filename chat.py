import os
import re
from datetime import date

import anthropic
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from settings import CLAUDE_MODEL, EMBEDDING_MODEL, PINECONE_INDEX, QUESTION_PREFIX, STRICT_YEAR_FOLDERS, YEAR_PATTERN

PASSAGES_PER_QUESTION = 8

# When a question mentions a year, this many passages are fetched and then reordered by year, so a
# passage from that year's file can move up even if it wasn't among the best PASSAGES_PER_QUESTION matches.
CANDIDATES_PER_YEAR_QUESTION = 24

SYSTEM_PROMPT = """You answer questions about the user's own files in their Google Drive.
With each question you get the parts of their files that best match it, inside <document> tags.

How to answer:
- Answer only from those documents. Don't guess or use outside knowledge about the user's things.
- Talk about the user's files by name ("your Television doc says..."). Never mention documents, \
passages, excerpts, tags, or searching; the user doesn't see those.
- If the documents don't answer the question, say you couldn't find it in their files and stop. \
Don't offer information about other things they didn't ask about.
- End with the names of the files you used.

Spreadsheets:
- One document per spreadsheet lists its tabs and each tab's column headings. Use it to explain how a file is laid out.
- Others list the spreadsheet's named ranges (like CURRENT_YEAR = Constants!B1 -> 2026), or one named \
function each, with its definition and sometimes a plain description.
- The rest start with the file, tab and column headings, then list cells like \
"C5 (Amount): =SUM(C2:C4) -> 1,250": cell C5, in the Amount column, holds that formula and shows that value.
- A formula used in several cells is written once under "Formulas:" as [Formula n], and its cells say \
"[Formula n]". It's written as it appears in its first cell; in other cells its references shift the way \
copied formulas do (one row down means row numbers go up by one, except ones with $).
- When asked about a formula, explain in plain words what it calculates and which cells it uses.
- You may only see part of a tab. Don't say a tab has nothing else in it unless the document shows the whole tab."""


# Words that mean a year counted from today, and how many years from today each one is.
RELATIVE_YEARS = {"this": 0, "current": 0, "last": -1, "previous": -1, "prior": -1, "next": 1}
RELATIVE_YEAR = re.compile(r"\b(" + "|".join(RELATIVE_YEARS) + r")\s+year\b", re.IGNORECASE)


def question_years(question, today=None):
    """The years a question asks about, as text like {"2025"}.

    Counts written years like 2025, and words like "this year", "current year" and "last year",
    which are turned into years counted from today.
    """
    this_year = (today or date.today()).year
    years = set(re.findall(YEAR_PATTERN, question))
    years |= {str(this_year + RELATIVE_YEARS[m.lower()]) for m in RELATIVE_YEAR.findall(question)}
    return years


def year_rank(file_name, years):
    """0 if the file's path has a year from the question, 1 if it has no year, 2 if it only has other years."""
    file_years = set(re.findall(YEAR_PATTERN, file_name))
    if file_years & years:
        return 0
    return 2 if file_years else 1


def in_strict_folder(file_name):
    """True if the file is in one of the STRICT_YEAR_FOLDERS, or in a subfolder of one."""
    return any(file_name.startswith(f + "/") for f in STRICT_YEAR_FOLDERS)


def find_passages(index, model, question):
    """Return the stored passages closest in meaning to the question.

    If the question mentions a year (see question_years), more passages are fetched, and they're reordered: first ones whose
    file path (like "Tax/Tax 2025/W2.pdf") has that year, then ones with no year, then ones with
    other years. Within each of those, the best matches come first. Passages from STRICT_YEAR_FOLDERS
    files with only other years are left out. The top PASSAGES_PER_QUESTION are kept.
    """
    # Some embedding models expect text in front of questions (not passages).
    text = f"{QUESTION_PREFIX} {question}" if QUESTION_PREFIX else question
    vector = model.encode(text, normalize_embeddings=True)
    years = set(re.findall(YEAR_PATTERN, question))
    result = index.query(
        vector=vector.tolist(),
        top_k=CANDIDATES_PER_YEAR_QUESTION if years else PASSAGES_PER_QUESTION,
        include_metadata=True,
    )
    passages = [match.metadata for match in result.matches]
    if years:
        # sorted() keeps the original order for passages with the same rank, so best matches stay first.
        passages = [p for p in passages
                    if not (in_strict_folder(p["file_name"]) and year_rank(p["file_name"], years) == 2)]
        passages = sorted(passages, key=lambda p: year_rank(p["file_name"], years))
    return passages[:PASSAGES_PER_QUESTION]


def connect():
    """Return the embedding model, the Pinecone index and the Claude client that answering questions needs."""
    model = SentenceTransformer(EMBEDDING_MODEL)
    index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(PINECONE_INDEX)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return model, index, client


def ask_claude(client, question, passages):
    """Send the question and passages to Claude, giving back the answer piece by piece as it arrives."""
    passage_text = "\n\n".join(
        f'<document file="{p["file_name"]}">\n{p["text"]}\n</document>' for p in passages
    )
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{passage_text}\n\nQuestion: {question}"}],
    ) as stream:
        yield from stream.text_stream
        if stream.get_final_message().stop_reason == "refusal":
            yield "(Claude declined to answer this question.)"


def main():
    model, index, client = connect()

    print("Ask a question about your files. Press Enter on an empty line to quit.")
    while True:
        question = input("\n> ").strip()
        if not question:
            return
        try:
            for text in ask_claude(client, question, find_passages(index, model, question)):
                print(text, end="", flush=True)
            print()
        except anthropic.APIError as e:
            print(f"Error from Claude: {e}")


if __name__ == "__main__":
    main()
