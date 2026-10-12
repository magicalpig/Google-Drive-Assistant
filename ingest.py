import io
import os
import sys

from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from drive import get_drive, list_my_files

INDEX_NAME = "chatbot"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # small (~130 MB), makes 384 numbers per passage
CHUNK_SIZE = 1000  # characters per passage, roughly a few paragraphs

GOOGLE_DOC = "application/vnd.google-apps.document"
PDF = "application/pdf"


def get_text(drive, file):
    """Download a Google Doc or PDF from Drive and return its text."""
    if file["mimeType"] == GOOGLE_DOC:
        data = drive.files().export(fileId=file["id"], mimeType="text/plain").execute()
        return data.decode("utf-8", errors="replace")
    data = drive.files().get_media(fileId=file["id"]).execute()
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def split_text(text):
    """Split text into passages of about CHUNK_SIZE characters, keeping paragraphs together."""
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
    """Return the Pinecone index, creating it the first time."""
    if not pc.has_index(INDEX_NAME):
        pc.create_index(
            name=INDEX_NAME,
            dimension=dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
    return pc.Index(INDEX_NAME)


def main():
    # Optional: "python ingest.py 5" only does the first 5 files, for testing.
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    load_dotenv()
    drive = get_drive()
    model = SentenceTransformer(EMBEDDING_MODEL)
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = get_index(pc, model.get_embedding_dimension())

    files = [f for f in list_my_files(drive) if f["mimeType"] in (GOOGLE_DOC, PDF)]
    files = files[:limit]
    print(f"{len(files)} files to process")

    for n, file in enumerate(files, 1):
        try:
            chunks = split_text(get_text(drive, file))
        except Exception as e:
            print(f"[{n}/{len(files)}] SKIPPED {file['name']}: {e}")
            continue
        if not chunks:
            print(f"[{n}/{len(files)}] no text  {file['name']}")
            continue

        vectors = model.encode(chunks, normalize_embeddings=True)
        records = [
            {
                "id": f"{file['id']}-{i}",
                "values": vector.tolist(),
                "metadata": {"file_name": file["name"], "file_id": file["id"], "text": chunk},
            }
            for i, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        for start in range(0, len(records), 100):
            index.upsert(vectors=records[start:start + 100])
        print(f"[{n}/{len(files)}] {len(chunks):4} passages  {file['name']}")


if __name__ == "__main__":
    main()
