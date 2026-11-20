import os

import anthropic
from dotenv import load_dotenv
from flask import Flask, Response, request, send_file, stream_with_context
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from chat import ask_claude, find_passages
from settings import EMBEDDING_MODEL, PINECONE_INDEX

load_dotenv()
model = SentenceTransformer(EMBEDDING_MODEL)
index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(PINECONE_INDEX)
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

app = Flask(__name__)


@app.get("/")
def home():
    return send_file("index.html")


@app.post("/ask")
def ask():
    question = request.json["question"].strip()

    def answer():
        try:
            yield from ask_claude(client, question, find_passages(index, model, question))
        except anthropic.APIError as e:
            yield f"Error from Claude: {e}"

    return Response(stream_with_context(answer()), mimetype="text/plain")


if __name__ == "__main__":
    # Only reachable from this computer (localhost).
    app.run(port=5000)
