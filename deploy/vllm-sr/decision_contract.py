"""Check captured sr:decision choice responses without calling any service.

Usage: python3 deploy/vllm-sr/decision_contract.py REQUEST.json RESPONSE.json
Exit 0 proves only the typed choice contract, not latency, accuracy, or provenance.
The summary never prints request content or upstream error text.
"""
import argparse
import json
from pathlib import Path

MODEL = "vllm-sr/decision"


def check(request, response):
    """Validate the external wire format. Return bounded, content-free failures."""
    try:
        if request["model"] != MODEL:
            return ["request_model_mismatch"]
        user = next(m for m in reversed(request["messages"]) if m["role"] == "user")
        questions = json.loads(user["content"])["questions"]
        if not isinstance(questions, dict) or not questions:
            return ["invalid_questions"]
        for question in questions.values():
            if (question["type"] != "choice" or not isinstance(question["criteria"], dict)
                    or not question["criteria"]):
                return ["unsupported_or_invalid_question"]
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return ["invalid_request"]

    try:
        if "error" in response:
            return ["upstream_error"]
        if response["model"] != MODEL or response["object"] != "chat.completion":
            return ["response_envelope_mismatch"]
        if len(response["choices"]) != 1:
            return ["expected_one_completion"]
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop" or choice["message"]["role"] != "assistant":
            return ["incomplete_or_invalid_completion"]
        answers = json.loads(choice["message"]["content"])
        if not isinstance(answers, dict) or answers.keys() != questions.keys():
            return ["question_coverage_mismatch"]
        for qid, question in questions.items():
            answer = answers[qid]
            if not isinstance(answer, dict) or "error" in answer:
                return ["invalid_answer"]
            if answer["choice"] not in question["criteria"]:
                return ["choice_outside_criteria"]
            confidence = answer.get("confidence", answer.get("score"))
            if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                return ["invalid_confidence"]
    except (KeyError, IndexError, TypeError, ValueError):
        return ["invalid_response"]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    args = parser.parse_args()
    try:
        failures = check(json.loads(args.request.read_text()), json.loads(args.response.read_text()))
    except (OSError, ValueError):
        failures = ["unreadable_or_invalid_capture"]
    print(json.dumps({"typed_choice_contract": not failures, "failures": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
