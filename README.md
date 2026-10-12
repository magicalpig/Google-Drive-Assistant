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

Each question costs about $0.0004–0.001 in Anthropic credits (Claude Haiku 5.5).

## How a question is answered

1. **Your question is turned into numbers.** The embedding model (`BAAI/bge-base-en-v1.5`) turns the question into a list of 768 numbers called a vector. Text with similar meaning gets similar numbers, even when the words differ. The model file is downloaded once (about 440 MB), and the `sentence-transformers` library uses it on your own computer.
2. **Pinecone finds the closest passages.** Pinecone compares the question's vector with the stored passage vectors and returns the 8 closest passages. If the question mentions a year, like 2025, files in other tax-year folders are left out. Files outside the tax-year folders are always included.
3. **Claude writes the answer.** The question and those 8 passages are sent to Claude, which answers using only the passages and lists the files it used. If the passages don't contain the answer, it says it couldn't find it and stops. Its instructions are `SYSTEM_PROMPT` in `chat.py`.

## What's stored in Pinecone

Everything is in one Pinecone index named `chatbot`. Docs, PDFs and .txt files are split into passages of about 1,000 characters. Every passage starts with its file name, so a question can match on the name as well as the contents.

### Google Sheets

Spreadsheets are handled by `sheets.py`. Their passages are up to about 2,000 characters, which is as much as the embedding model reads. Each spreadsheet gets:

- **An overview passage:** its tabs, how many rows each has, and each tab's column headings. The headings row is the one among the first 5 rows with the most text cells.
- **Named range passages:** each name, the cell it points to, and that cell's value, for example `CURRENT_YEAR = Constants!B1 -> 2026`.
- **One passage per named function:** its full definition. Google's Sheets API doesn't return named functions, so they're read from Google's Excel export of the file.
- **Cell passages:** each starts with the file name, tab name and column headings. Each cell is labeled with its column heading, for example `D3 (Amount): $357.50`.

**Repeated formulas are written once.** Formulas that are the same apart from shifted cell references, like `=D2/65` in F2 and `=D3/65` in F3, count as one formula. This works the way copying a formula works: references without `$` shift, references with `$` stay fixed. A formula used in several cells is named, like `[Formula 2]`, and written out once at the top of each passage that uses it. Its cells say `F3 (Hours worked): [Formula 2] -> 5.50`. A formula used in only one cell is written in that cell's line.

**Formula descriptions (costs credits):** for spreadsheets listed in `DESCRIBE_FORMULAS_IN` in `sheets.py`, ingest asks Claude (`DESCRIBE_MODEL`, now Opus 5.5) to write a plain-English description of each formula and named function. For example: "Hours worked = this row's Amount ÷ 65." It makes one request per spreadsheet and prints what it cost. The HSA Transaction report cost about $0.06. The listed spreadsheets are:

- Payroll 2026
- Business Expenses 2026
- HSA Transaction report 2026

For those spreadsheets, the descriptions are what get embedded, so questions in everyday words can find formulas. For other spreadsheets, the first 150 characters of each raw formula are embedded instead. Either way, Claude reads the full raw formula when answering.

A spreadsheet is only described again when it changes in Drive, or when `PASSAGE_FORMAT` is raised.

### What each record holds

Each passage is stored as one record with:

- its vector (the 768 numbers from the embedding model)
- the passage text that Claude reads. For spreadsheets, this can differ from the text that was embedded.
- the file name with its folder path (like `Tax/Tax 2025/W2.pdf`), and its Google Drive file ID
- the tax year of its folder, or 0 if it isn't in a `Tax/Tax <year>` folder
- the file's last-modified time in Drive
- the passage format number (`PASSAGE_FORMAT` in `ingest.py`)

If `EMBEDDING_MODEL` in `ingest.py` is changed to a model with a different number of numbers per vector, the next ingest run deletes the index, makes it again, and stores every file again.

Each record's ID is the Drive file ID plus the passage number, for example `1bOQuT6...-0`, `1bOQuT6...-1`.

## Adding new files and updates

Run `ingest.py` again whenever your Drive changes:

```
python ingest.py
```

For each file, it compares the file's last-modified time in Drive with the time stored in Pinecone:

- **New file:** reads, embeds and stores it.
- **Same time and same passage format:** skips it.
- **Different time or older passage format:** deletes the file's old passages and stores it again.
- **No longer in Drive:** deletes its passages.

When the code that makes passages changes, raise `PASSAGE_FORMAT` in `ingest.py`. The next run then stores every file again in the new format.

Embedding uses the computer's processor heavily. On an old PC, run it in the codespace instead.

To test on just the first few files, add a number: `python ingest.py 5`. A test run like this doesn't delete passages for removed files.

## Which Drive files are included

Only files that meet all of these:

- You own them. Files shared with you are left out, including files in shared drives.
- They aren't in the trash.
- They aren't in an old tax-year folder. Files in a subfolder of **Tax** named for a year before 2025, like `Tax/Tax 2021`, are skipped, and their passages are removed from Pinecone. The cutoff is `EARLIEST_TAX_YEAR` in `ingest.py`.
- They are one of these types:
  - **Google Docs:** the text
  - **Google Sheets:** every non-empty cell in every tab, with its formula and the value it shows, plus named ranges and named functions. Apps Script functions aren't read.
  - **PDFs:** the text only. Scanned PDFs are pictures of pages and have no text, so they're skipped. One PDF can't be read because `pypdf` fails on a picture inside it.
  - **.txt files**

Not included: Word documents, Excel files (.xlsx and .xls), CSV files, audio, images, video and all other types.

Other limits:

- The embedding model is built for English. Questions about files in other languages, such as Portuguese, will find poor matches.
- Claude sees only the 8 passages that best match each question, never a whole file. For a large spreadsheet, that's only part of it.

## Services this project uses

Prices and limits below are as of October 2026. Check each service's site for changes.

### Google Cloud: access to your Drive

A Google Cloud project named `chatbot` (console.cloud.google.com) lets the scripts read your Drive.

- **APIs turned on:** Google Drive API (lists files and downloads Docs, PDFs and .txt files) and Google Sheets API (reads each cell's formula and value).
- **Sign-in screen** (Google Auth Platform): set to External and Testing. Only accounts listed as test users can sign in, and your Gmail is the only one.
- **OAuth client:** a Desktop app. Its file is `credentials.json`.
- **Permission asked for:** read-only access to Drive (`drive.readonly`). The scripts can't change or delete anything.
- **Cost:** free.
- **Limit:** while the app is in Testing, Google ends the saved sign-in after 7 days. When that happens, the scripts fail with an `invalid_grant` error. Delete `token.json` and run `python drive.py` to sign in again. Then copy the new `token.json` to any codespace that needs it.

### GitHub: the code and the codespace

The code is in the private repository `magicalpig/chatbot` on GitHub, which is free.

A **codespace** is a computer in the cloud with a copy of this repository on it. You open it in your browser or in VS Code. It's used to run `ingest.py` without loading your own PC.

How it's connected to the repository:

- It was created from the repository, so it started with a copy of the code.
- `.devcontainer/devcontainer.json` tells GitHub to set it up with Python 3.12.
- It doesn't update by itself. After pushing changes from the desktop or laptop, run `git pull` in the codespace to get them. If `requirements.txt` changed, also run `pip install -r requirements.txt`.
- `.env`, `credentials.json` and `token.json` aren't on GitHub, so they were copied into the codespace by hand.
- Installed libraries, the downloaded embedding model and the copied files stay in the codespace between uses. They are lost only if the codespace is deleted.

Limits on a free personal account:

- **Time:** 120 core-hours a month. The codespace has 2 cores, so that's 60 hours of running time. Time counts only while it's running.
- **Storage:** 15 GB a month.
- **Stopping:** it stops by itself after 30 minutes without use. You can also stop it at github.com/codespaces.
- **Beyond the free allowance:** about $0.18 an hour for 2 cores, but only if you add a payment method. Without one, it just stops working until next month.
- **Checking usage:** github.com/settings/billing/usage. The amount shown there before the "discount" line is what it would cost without the free allowance.

### Anthropic: Claude

Claude writes the answers. The key in `.env` must be created inside a workspace in the Anthropic Console (console.anthropic.com). A key assigned to the whole organization doesn't work with this code.

- **Billing:** you buy credits in advance, and each question uses some. The Claude Pro plan doesn't cover this. API credits are billed separately.
- **Model:** Claude Haiku 5.5, set by `CLAUDE_MODEL` in `chat.py`. It costs $0.10 per million tokens sent and $0.50 per million tokens received. A token is about three-quarters of a word.
- **Per question:** about $0.0004–0.001, depending on how many long spreadsheet passages are found. $1 covers 1,000 or more questions.
- **Ingest:** describing formulas in the spreadsheets listed in `sheets.py` uses Opus 5.5 at $4 per million tokens sent and $20 per million received. That's a few cents to a few tenths of a dollar per spreadsheet, and only when the spreadsheet is new or has changed. Ingest prints each cost.
- **Spending limit:** set a monthly limit in the Console under Plans & Billing.
- **When credits run out:** answers fail with "Your credit balance is too low."

### Pinecone: the stored passages

Pinecone (pinecone.io) stores the passages and their vectors. It's on the free Starter plan.

- **Storage:** 2 GB. About 8,300 passages use a small part of it.
- **Indexes:** up to 5. This project uses 1, in AWS us-east-1, the only region on the free plan.
- **Monthly usage:** up to 2 million write units (storing, updating and deleting) and 1 million read units (searches and lookups). If either runs out, Pinecone refuses requests until the next month.
- **Cost:** free. Free indexes aren't deleted for lack of use.

### Hugging Face: the embedding model

The embedding model `BAAI/bge-base-en-v1.5` is downloaded from Hugging Face the first time a script needs it, then kept on that computer. It's free and doesn't need an account. You can ignore the "unauthenticated requests to the HF Hub" warning.
