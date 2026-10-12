import io
import os
import re
import sys

import anthropic
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from drive import get_drive, get_sheets, list_my_files
from sheets import sheet_passages

INDEX_NAME = "chatbot"
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"  # ~440 MB, makes 768 numbers per passage
CHUNK_SIZE = 1000  # characters per passage, roughly a few paragraphs

# Raise this when the way passages are made changes, so the next run stores every file again.
# 2: passages start with the file name; Sheets passages also have the tab name and column headings.
# 3: Sheets formulas are grouped and named, with named ranges, named functions and descriptions.
#    File names include their folder path, and tax files have a "year".
PASSAGE_FORMAT = 3

# Files in a subfolder of Tax named for a year before this one (like "Tax/Tax 2021") are skipped.
EARLIEST_TAX_YEAR = 2025

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
PDF = "application/pdf"
TEXT = "text/plain"


def tax_year(file):
    """The year of the Tax subfolder a file is in, like 2021 for "Tax/Tax 2021/W2.pdf". 0 if none."""
    parts = file["folder"].split("/")
    if len(parts) < 2 or parts[0] != "Tax":
        return 0
    match = re.search(r"(?<!\d)(?:19|20)\d\d(?!\d)", parts[1])
    return int(match.group()) if match else 0


def full_name(file):
    """The file's name with its folder path, like "Tax/Tax 2025/W2.pdf"."""
    return f"{file['folder']}/{file['name']}" if file["folder"] else file["name"]


def clean(text):
    """Replace broken characters (found in some PDFs and Sheets) that crash the embedding model with "?"."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def get_passages(drive, sheets, client, file):
    """Download a Google Doc, Google Sheet, PDF, or .txt file from Drive and split it into passages.

    Returns (text to embed, text Claude reads) pairs. For everything except Sheets, the two are
    the same. Every passage starts with the file's folder path and name, so questions can match
    on those too.
    """
    if file["mimeType"] == GOOGLE_SHEET:
        passages = sheet_passages(drive, sheets, client, file)
        return [(clean(embed), clean(read)) for embed, read in passages]
    if file["mimeType"] == GOOGLE_DOC:
        data = drive.files().export(fileId=file["id"], mimeType="text/plain").execute()
        text = data.decode("utf-8", errors="replace")
    else:
        data = drive.files().get_media(fileId=file["id"]).execute()
        if file["mimeType"] == TEXT:
            text = data.decode("utf-8", errors="replace")
        else:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    passages = [f"File: {full_name(file)}\n{passage}" for passage in split_text(text)]
    return [(passage, passage) for passage in passages]


def split_text(text):
    """Split text into passages of about CHUNK_SIZE characters, keeping paragraphs together."""
    text = clean(text)
    chunks = []
    current = ""
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # A paragraph longer than CHUNK_SIZE gets cut into pieces.
        while len(paragraph) > CHUNK_SIZE:
            chunks.append(paragraph[:CHUNK_SIZE])
            paragraph = paragraph[CHUNK_SIZE:]
        if len(current) + len(paragraph) > CHUNK_SIZE:
            chunks.append(current)
            current = ""
        current += paragraph + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def get_index(pc, dimension):
    """Return the Pinecone index, creating it the first time.

    If the index was made for a different embedding model (vectors of a different size), it's
    deleted and made again, and every file is stored again.
    """
    if pc.has_index(INDEX_NAME) and pc.describe_index(INDEX_NAME).dimension != dimension:
        print(f"The embedding model changed: deleting the Pinecone index '{INDEX_NAME}' to make it again")
        pc.delete_index(INDEX_NAME)
    if not pc.has_index(INDEX_NAME):
        pc.create_index(
            name=INDEX_NAME,
            dimension=dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
    return pc.Index(INDEX_NAME)


def remove_deleted_files(index, current_file_ids):
    """Delete passages of files that are no longer in Drive."""
    stored_file_ids = set()
    for page in index.list():
        for item in page.vectors:
            stored_file_ids.add(item.id.rsplit("-", 1)[0])  # "<file id>-<passage number>"
    for file_id in stored_file_ids - current_file_ids:
        index.delete(filter={"file_id": file_id})
        print(f"removed deleted file  {file_id}")


def main():
    # Optional: "python ingest.py 5" only does the first 5 files, for testing.
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    load_dotenv()
    drive = get_drive()
    sheets = get_sheets()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])  # describes formulas in a few Sheets
    model = SentenceTransformer(EMBEDDING_MODEL)
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = get_index(pc, model.get_embedding_dimension())

    files = [f for f in list_my_files(drive) if f["mimeType"] in (GOOGLE_DOC, GOOGLE_SHEET, PDF, TEXT)]
    old_tax = [f for f in files if 0 < tax_year(f) < EARLIEST_TAX_YEAR]
    files = [f for f in files if f not in old_tax][:limit]
    print(f"{len(files)} files to process ({len(old_tax)} skipped in Tax folders before {EARLIEST_TAX_YEAR})")

    if limit is None:
        remove_deleted_files(index, {f["id"] for f in files})

    for n, file in enumerate(files, 1):
        first = index.fetch(ids=[f"{file['id']}-0"]).vectors.get(f"{file['id']}-0")
        if first:
            if (first.metadata.get("modified") == file["modifiedTime"]
                    and first.metadata.get("format") == PASSAGE_FORMAT):
                print(f"[{n}/{len(files)}] up to date  {file['name']}")
                continue
            # The file changed, or its passages were made the old way: remove them, then store it again.
            index.delete(filter={"file_id": file["id"]})
        try:
            passages = get_passages(drive, sheets, client, file)
            if not passages:
                print(f"[{n}/{len(files)}] no text  {file['name']}")
                continue
            vectors = model.encode([embed for embed, _ in passages], normalize_embeddings=True)
        except Exception as e:
            print(f"[{n}/{len(files)}] SKIPPED {file['name']}: {e}")
            continue

        records = [
            {
                "id": f"{file['id']}-{i}",
                "values": vector.tolist(),
                "metadata": {
                    "file_name": full_name(file),
                    "file_id": file["id"],
                    "year": tax_year(file),  # 0 unless the file is in a Tax subfolder for a year
                    "modified": file["modifiedTime"],
                    "format": PASSAGE_FORMAT,
                    # Pinecone allows 40 KB of these details per passage; long formulas could pass that.
                    "text": read[:30000],
                },
            }
            for i, ((_, read), vector) in enumerate(zip(passages, vectors))
        ]
        for start in range(0, len(records), 100):
            index.upsert(vectors=records[start:start + 100])
        print(f"[{n}/{len(files)}] {len(passages):4} passages  {file['name']}")


if __name__ == "__main__":
    main()
