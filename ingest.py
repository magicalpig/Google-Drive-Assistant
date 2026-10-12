import io
import os
import sys

from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from drive import get_drive, get_sheets, list_my_files

INDEX_NAME = "chatbot"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # small (~130 MB), makes 384 numbers per passage
CHUNK_SIZE = 1000  # characters per passage, roughly a few paragraphs

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
PDF = "application/pdf"
TEXT = "text/plain"


def column_letter(n):
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    n += 1
    while n:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def get_sheet_text(sheets, file_id):
    """Return every non-empty cell of a Google Sheet, with formulas, like 'C5: =SUM(C2:C4) -> 1,250'."""
    result = sheets.spreadsheets().get(
        spreadsheetId=file_id,
        includeGridData=True,
        fields="sheets(properties.title,data.rowData.values(userEnteredValue,formattedValue))",
    ).execute()
    lines = []
    for sheet in result.get("sheets", []):
        lines.append(f"Sheet: {sheet['properties']['title']}")
        for grid in sheet.get("data", []):
            for row_num, row in enumerate(grid.get("rowData", []), 1):
                for col_num, cell in enumerate(row.get("values", [])):
                    shown = cell.get("formattedValue")
                    formula = cell.get("userEnteredValue", {}).get("formulaValue")
                    if formula:
                        lines.append(f"{column_letter(col_num)}{row_num}: {formula} -> {shown}")
                    elif shown:
                        lines.append(f"{column_letter(col_num)}{row_num}: {shown}")
    return "\n".join(lines)


def get_text(drive, sheets, file):
    """Download a Google Doc, Google Sheet, PDF, or .txt file from Drive and return its text."""
    if file["mimeType"] == GOOGLE_SHEET:
        return get_sheet_text(sheets, file["id"])
    if file["mimeType"] == GOOGLE_DOC:
        data = drive.files().export(fileId=file["id"], mimeType="text/plain").execute()
        return data.decode("utf-8", errors="replace")
    data = drive.files().get_media(fileId=file["id"]).execute()
    if file["mimeType"] == TEXT:
        return data.decode("utf-8", errors="replace")
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def split_text(text):
    """Split text into passages of about CHUNK_SIZE characters, keeping paragraphs together."""
    # Some PDFs contain broken characters that crash the embedding model; replace them with "?".
    text = text.encode("utf-8", errors="replace").decode("utf-8")
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
    model = SentenceTransformer(EMBEDDING_MODEL)
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = get_index(pc, model.get_embedding_dimension())

    files = [f for f in list_my_files(drive) if f["mimeType"] in (GOOGLE_DOC, GOOGLE_SHEET, PDF, TEXT)]
    files = files[:limit]
    print(f"{len(files)} files to process")

    if limit is None:
        remove_deleted_files(index, {f["id"] for f in files})

    for n, file in enumerate(files, 1):
        first = index.fetch(ids=[f"{file['id']}-0"]).vectors.get(f"{file['id']}-0")
        if first:
            stored_modified = first.metadata.get("modified")
            if stored_modified == file["modifiedTime"]:
                print(f"[{n}/{len(files)}] up to date  {file['name']}")
                continue
            if stored_modified is None:
                # Stored by the first version of this script, which didn't save dates.
                # Those files were stored on 2026-10-12, so just add the current date.
                index.update(filter={"file_id": file["id"]}, set_metadata={"modified": file["modifiedTime"]})
                print(f"[{n}/{len(files)}] added date  {file['name']}")
                continue
            # The file changed since it was stored: remove its old passages, then store it again.
            index.delete(filter={"file_id": file["id"]})
        try:
            chunks = split_text(get_text(drive, sheets, file))
            if not chunks:
                print(f"[{n}/{len(files)}] no text  {file['name']}")
                continue
            vectors = model.encode(chunks, normalize_embeddings=True)
        except Exception as e:
            print(f"[{n}/{len(files)}] SKIPPED {file['name']}: {e}")
            continue

        records = [
            {
                "id": f"{file['id']}-{i}",
                "values": vector.tolist(),
                "metadata": {
                    "file_name": file["name"],
                    "file_id": file["id"],
                    "modified": file["modifiedTime"],
                    "text": chunk,
                },
            }
            for i, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        for start in range(0, len(records), 100):
            index.upsert(vectors=records[start:start + 100])
        print(f"[{n}/{len(files)}] {len(chunks):4} passages  {file['name']}")


if __name__ == "__main__":
    main()
