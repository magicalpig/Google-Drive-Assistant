"""Measure how well the search makes use of the right files, using test questions you write.

Put the test questions in eval_questions.json (see eval_questions.example.json). Each one names a file
that should be among the passages found for it. For each question this prints where that file's first
passage came in the results, then two totals:
- hit rate: the share of questions whose file was found at all
- mean reciprocal rank (MRR): the average of 1/position, so 1.0 means the file always came first

Only the search runs, so this costs nothing in Claude API credits.
"""
import json
import sys

from chat import PASSAGES_PER_QUESTION, connect, find_passages

QUESTIONS_FILE = "eval_questions.json"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else QUESTIONS_FILE
    with open(path, encoding="utf-8") as f:
        tests = json.load(f)
    model, index, _ = connect()

    hits, reciprocal_ranks = 0, []
    for test in tests:
        file_names = [p["file_name"] for p in find_passages(index, model, test["question"])]
        # A file matches if the expected name is part of its name, so "W2.pdf" matches "Tax/Tax 2025/W2.pdf".
        rank = next((i for i, name in enumerate(file_names, 1) if test["expected_file"] in name), None)
        if rank:
            hits += 1
        reciprocal_ranks.append(1 / rank if rank else 0)
        print(f"{f'#{rank}' if rank else 'missed':>6}  {test['question']}")
        if not rank:
            # The files the search found instead, best match first, each listed once.
            for name in dict.fromkeys(file_names):
                print(f"        found: {name}")

    print(f"\nhit rate (top {PASSAGES_PER_QUESTION}): {hits}/{len(tests)} = {hits / len(tests):.0%}")
    print(f"mean reciprocal rank: {sum(reciprocal_ranks) / len(tests):.2f}")


if __name__ == "__main__":
    main()
