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

SYSTEM_PROMPT = """You answer questions about the user's own files from their Google Drive.
You are given passages from those files. Answer using only those passages.
After your answer, list the file names you used.
If the passages don't contain the answer, say so plainly instead of guessing.
Spreadsheet passages list cells like "C5: =SUM(C2:C4) -> 1250", meaning cell C5 holds that formula and shows that value.
When asked about a formula, explain in plain words what it calculates."""


def find_passages(index, model, question):
    """Return the stored passages closest in meaning to the question."""
    vector = model.encode(QUESTION_PREFIX + question, normalize_embeddings=True)
    result = index.query(vector=vector.tolist(), top_k=PASSAGES_PER_QUESTION, include_metadata=True)
    return [match.metadata for match in result.matches]


def ask_claude(client, question, passages):
    """Send the question and passages to Claude and print the answer as it arrives."""
    passage_text = "\n\n".join(
        f'<passage file="{p["file_name"]}">\n{p["text"]}\n</passage>' for p in passages
    )
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{passage_text}\n\nQuestion: {question}"}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        if stream.get_final_message().stop_reason == "refusal":
            print("(Claude declined to answer this question.)", end="")
    print()


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
            ask_claude(client, question, find_passages(index, model, question))
        except anthropic.APIError as e:
            print(f"Error from Claude: {e}")


if __name__ == "__main__":
    main()
