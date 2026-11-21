"""Settings read from .env, with defaults for anything left out. See .env.example."""
import os

from dotenv import load_dotenv

load_dotenv()


def setting(name, default):
    """The value of `name` in .env, or `default` if it's missing or empty."""
    return os.environ.get(name, "").strip() or default


# Name of the Pinecone index that stores the passages. ingest.py creates it if it doesn't exist.
PINECONE_INDEX = setting("PINECONE_INDEX", "google-drive-assistant")

# Claude model that answers questions.
CLAUDE_MODEL = setting("CLAUDE_MODEL", "claude-haiku-5-5")

# Claude model that describes formulas, and its prices in dollars per million tokens sent and
# received (only used to print what describing cost).
DESCRIBE_MODEL = setting("DESCRIBE_MODEL", "claude-opus-5-5")
DESCRIBE_PRICES = tuple(float(p) for p in setting("DESCRIBE_PRICES", "4,20").split(","))

# Spreadsheets whose formulas are described, separated by | since file names often have commas.
DESCRIBE_FORMULAS_IN = [n.strip() for n in setting("DESCRIBE_FORMULAS_IN", "").split("|") if n.strip()]

# Folders whose files (including ones in their subfolders) are never loaded, separated by |.
# Each is a path from My Drive, like "Old stuff" or "Work/Archive".
IGNORE_FOLDERS = [n.strip().strip("/") for n in setting("IGNORE_FOLDERS", "").split("|") if n.strip()]

# Folders where answers must come from the right year, separated by |, as paths from My Drive like "Tax".
# When a question mentions a year, passages from files in these folders (or their subfolders) whose path
# only has other years are left out, instead of just moved down.
STRICT_YEAR_FOLDERS = [n.strip().strip("/") for n in setting("STRICT_YEAR_FOLDERS", "").split("|") if n.strip()]

# A year from 1900 to 2099 that isn't part of a longer number.
YEAR_PATTERN = r"(?<!\d)(?:19|20)\d\d(?!\d)"

# Embedding model, from Hugging Face. Passages stored with a different model are stored again.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
EMBEDDING_MODEL = setting("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

# Text put in front of each question before embedding it. bge models expect this one.
BGE_QUESTION_PREFIX = "Represent this sentence for searching relevant passages:"
QUESTION_PREFIX = setting("QUESTION_PREFIX", BGE_QUESTION_PREFIX if "bge" in EMBEDDING_MODEL.lower() else "")
