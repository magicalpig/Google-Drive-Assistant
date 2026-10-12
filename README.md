# Chatbot

Answers questions about your own Google Drive files. It finds the passages in your files that best match your question, then has Claude write an answer from those passages.

## Before you start

These files must be in the project folder. They are kept out of GitHub, so copy them yourself to each computer or codespace:

- `.env`: holds `ANTHROPIC_API_KEY` and `PINECONE_API_KEY`
- `credentials.json`: the Google Cloud sign-in file for this app
- `token.json`: your saved Google sign-in. It's created the first time you run `drive.py` or `ingest.py`.

Install the Python libraries once:

```
pip install -r requirements.txt
```

On the desktop, use the project's own Python at `.venv\Scripts\python` in place of `python` in the commands below.

## Start the website

```
python app.py
```

Wait for `Running on http://127.0.0.1:5000`, then open **http://localhost:5000** in your browser. Type a question and press Enter. Shift+Enter starts a new line.

The page can only be opened from the same computer. To stop it, press Ctrl+C in the terminal.

## Use it in the terminal

```
python chat.py
```

Type a question and press Enter. The answer prints as Claude writes it. Press Enter on an empty line to quit.

Each question costs about $0.0004 in Anthropic credits (Claude Haiku 5.5).

## How a question is answered

1. **Your question is turned into numbers.** The embedding model (`BAAI/bge-small-en-v1.5`) turns the question into a list of 384 numbers called a vector. Text with similar meaning gets similar numbers, even when the words differ. The model file is downloaded once (about 130 MB), and the `sentence-transformers` library uses it on your own computer.
2. **Pinecone finds the closest passages.** Pinecone compares the question's vector with the stored passage vectors and returns the 8 closest passages.
3. **Claude writes the answer.** The question and those 8 passages are sent to Claude, which answers using only the passages and lists the files it used. If the passages don't contain the answer, it says so.

## What's stored in Pinecone

Everything is in one Pinecone index named `chatbot`. Your files are split into passages of about 1,000 characters. Each passage is stored as one record with:

- its vector (the 384 numbers from the embedding model)
- the passage text
- the file name and Google Drive file ID
- the file's last-modified time in Drive

Each record's ID is the Drive file ID plus the passage number, for example `1bOQuT6...-0`, `1bOQuT6...-1`.

## Adding new files and updates

Run `ingest.py` again whenever your Drive changes:

```
python ingest.py
```

For each file, it compares the file's last-modified time in Drive with the time stored in Pinecone:

- **New file:** reads, embeds and stores it.
- **Same time:** skips it.
- **Different time:** deletes the file's old passages and stores it again.
- **No longer in Drive:** deletes its passages.

Embedding uses the computer's processor heavily. On an old PC, run it in the codespace instead.

To test on just the first few files, add a number: `python ingest.py 5`. A test run like this doesn't delete passages for removed files.

## Which Drive files are included

Only files that meet all of these:

- You own them. Files shared with you are left out, including files in shared drives.
- They aren't in the trash.
- They are one of these types:
  - **Google Docs:** the text
  - **Google Sheets:** every non-empty cell, with its formula and the value it shows, for example `C5: =SUM(C2:C4) -> 1,250`
  - **PDFs:** the text only. Scanned PDFs are pictures of pages and have no text, so they're skipped. One PDF can't be read because `pypdf` fails on a picture inside it.
  - **.txt files**

Not included: Word documents, Excel files (.xlsx and .xls), CSV files, audio, images, video and all other types.

Other limits:

- The embedding model is built for English. Questions about files in other languages, such as Portuguese, will find poor matches.
- Spreadsheet cells are stored as plain lists like `G8: =137.26+12.23 -> $149.49`, so questions in everyday words may not find the right cells.
