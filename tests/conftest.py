"""Shared setup for the tests.

Loading sentence-transformers also loads PyTorch, which takes about a minute. No test uses the
embedding model, so a stand-in is put in its place before chat.py and ingest.py import it.
"""
import sys
import types

sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=None))
