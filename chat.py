import os

import anthropic
from dotenv import load_dotenv
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from ingest import EMBEDDING_MODEL, INDEX_NAME

CLAUDE_MODEL = "claude-haiku-5-5"
PASSAGES_PER_QUESTION = 8

# The embedding model's makers recommend putting this in front of questions (not passages).
QUESTION_PREFIX = "Represent this sentence for searching relevant passages: "

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


def find_passages(index, model, question):
    """Return the stored passages closest in meaning to the question."""
    vector = model.encode(QUESTION_PREFIX + question, normalize_embeddings=True)
    result = index.query(vector=vector.tolist(), top_k=PASSAGES_PER_QUESTION, include_metadata=True)
    return [match.metadata for match in result.matches]


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
    load_dotenv()
    model = SentenceTransformer(EMBEDDING_MODEL)
    index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(INDEX_NAME)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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
