# Google Drive Assistant

Ask questions about your own Google Drive files and get answers from Claude, based on what's in those files.

For spreadsheets you choose, formulas are also passed through an LLM to capture what they mean, so the assistant can explain how values are calculated.

## What you need

| What | Why | Cost |
|---|---|---|
| **Claude API key** (console.anthropic.com) | Claude writes the answers to your questions, and generates plain-English descriptions of the formulas in any spreadsheets you choose. The models are set in `.env`. You buy API credits in advance. A Claude Pro plan doesn't include them. | About $0.001 per question, plus a few cents per spreadsheet you choose |
| **Pinecone account** (pinecone.io) | An online database that stores each passage's embedding, its text, and details like the file's name and folder. Searches compare embeddings, and Claude reads the text. It's set up automatically the first time you load files. | Free Starter plan |
| **Google Cloud project** (console.cloud.google.com) | Lets the scripts read your Drive (read-only). | Free |
| **Embedding model** from Hugging Face | Turns text into embeddings so passages can be matched to questions. It downloads by itself (the default is about 440 MB) the first time, on each computer that runs the scripts. Many models need no account. | Free and paid options exist |
| **Python 3.12** | Runs the scripts. | Free |
| **GitHub codespace** (optional) | A cloud computer for loading your files, if your own computer is too slow. See [Optional: use a codespace](#optional-use-a-codespace). | Free up to 60 hours a month |

The default embedding model is a small free model that runs on an ordinary computer. It was chosen to prove the idea works, not for the best possible matches, and it works with English only. For more accurate results, set `EMBEDDING_MODEL` to a larger model (see [Changing the models](#changing-the-models)).

### Changing the models

All three are set in `.env`. Leave a setting out to use the default. See `.env.example` for details.

- **Answering model:** `CLAUDE_MODEL`. Any Claude model works.
- **Formula model:** `DESCRIBE_MODEL`. Any Claude model works. Also set `DESCRIBE_PRICES`, so the printed costs stay right.
- **Embedding model:** `EMBEDDING_MODEL`. Any model the `sentence-transformers` library can load works. Then run `ingest.py`, which stores every file again with the new model.

## How it fits together

1. **Load your files** (`ingest.py`): reads your Drive files and splits them into passages (laying out spreadsheets by tab, column and formula), which are saved in Pinecone along with their embeddings. This keeps the processor busy for a long time.
2. **Ask questions** (`app.py` for a web page in your browser, or `chat.py` for the terminal): finds the best-matching text in Pinecone and sends it to Claude to write an answer. This needs little processing, because only your one short question is embedded, and Pinecone's computers do the searching.

## Set up

### 1. Google Cloud

1. Create a project at console.cloud.google.com.
2. Turn on the **Google Drive API** and the **Google Sheets API**.
3. Set up the sign-in screen (Google Auth Platform) as **External**, and add your Gmail address as a test user.
4. Create an OAuth client of type **Desktop app**. Download its file and save it in the project folder as `credentials.json`.

### 2. Keys

Copy `.env.example` to `.env` and fill in:

- `ANTHROPIC_API_KEY`: create it inside a workspace in the Anthropic Console. An organization-wide key won't work here.
- `PINECONE_API_KEY`: in app.pinecone.io, go to **API Keys** and create a key.
- `PINECONE_INDEX` (optional): the name of the Pinecone index that stores your passages. Default: `google-drive-assistant`. You don't have to create the index in Pinecone. `ingest.py` creates it the first time it runs. The name can use lowercase letters, numbers and hyphens. Don't use the name of an index you use for something else: if its embedding size doesn't match, `ingest.py` deletes it and makes a new one. To see your existing index names, go to **Database > Indexes** in app.pinecone.io.

### 3. Your computer

```
pip install -r requirements.txt
python drive.py
```

`drive.py` opens a browser window to sign in to Google, then saves the sign-in as `token.json`.

On Windows with the project's own Python, use `.venv\Scripts\python` instead of `python`.

## Load your files

```
python ingest.py
```

The first run stores every file and can take a long time. Later runs only handle new, changed and deleted files. Nothing runs this automatically, so run it whenever your Drive changes.

### Optional: use a codespace

On an old or slow computer, loading your files can take hours. A GitHub codespace is a cloud computer that can do it instead.
1. On the repository's GitHub page, click **Code**, then **Codespaces**, then **Create codespace on main**. It opens in your browser and looks like VS Code.
2. Drag `.env`, `credentials.json` and `token.json` from your computer into its file list. These files hold your keys and sign-in, so they're never put on GitHub.
3. In its terminal, run `pip install -r requirements.txt`, then `python ingest.py`.

To return to it later, go to github.com/codespaces. It keeps your files and libraries. It doesn't pick up code changes by itself, so run `git pull` there after you push changes.

Saved formula descriptions (`formula_descriptions.json`) stay on whichever computer loads the files. If you switch computers, copy that file over, or the formulas are described again at full cost.

## Ask questions

```
python app.py
```

Open **http://localhost:5000**, type a question and press Enter. To stop it, press Ctrl+C in the terminal.

Or, to ask questions in the terminal instead, run `python chat.py`.

## What gets read

- Google Docs, Google Sheets (cells, formulas, named ranges and named functions), PDFs with text, and .txt files.
- Only files you own. Files shared with you, including ones in shared drives, are left out.
- Files in the trash are left out.
- Files in `Tax/Tax <year>` folders before 2025 are skipped (`EARLIEST_TAX_YEAR` in `ingest.py`). Admittedly, this is very specific to the author's own use case. It keeps answers about taxes limited to recent years, so they're accurate, and it saves the tokens needed for Claude to generate formula descriptions for old tax spreadsheets.
- Not read: Word, Excel, CSV, scanned PDFs, images, audio and video.

Claude sees only the 8 best-matching passages for each question, never a whole file.

### Optional: plain-English formula descriptions

Without this, the assistant sees each formula only as raw text, like `=SUMIFS(D:D,A:A,">="&B1)`. That's hard to connect to a question like "how is my quarterly tax figured?". For the spreadsheets you list, Claude describes each formula in plain English while your files load, so the assistant can locate and explain them. It's worth it for spreadsheets with many or complicated formulas.

It costs a few cents per spreadsheet, and only new or changed formulas are described again.

List the spreadsheets in `DESCRIBE_FORMULAS_IN` in `.env`, separated by `|` (commas are common in file names, so they aren't used):

```
DESCRIBE_FORMULAS_IN=Budget 2026|Smith, J. payroll|Mileage log
```

- Folders aren't considered. If you list "Income Tax", every Google Sheet named "Income Tax", in any folder, gets its formulas described.
- Only Google Sheets. Excel files (.xlsx) in your Drive aren't read at all.
- Names must match exactly, including capitals. Spaces around each name are ignored.
- A file name that contains `|` can't be listed.
- Leave it empty to describe none.

## When something stops working

- **`invalid_grant` error:** Google ends the sign-in after 7 days while the Google Cloud app is in Testing. Delete `token.json`, run `python drive.py` again. If you use a codespace, copy the new `token.json` to it.
- **"Your credit balance is too low":** buy more credits in the Anthropic Console.
- **Codespace won't start:** the free 60 hours for the month may be used up. Check github.com/settings/billing/usage.
