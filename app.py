import anthropic
from flask import Flask, Response, request, send_file, stream_with_context

from chat import ask_claude, connect, find_passages

model, index, client = connect()

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
