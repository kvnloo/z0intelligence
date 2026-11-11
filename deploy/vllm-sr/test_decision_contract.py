"""Offline contract checks. No inference, keys, or external calls."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("decision_contract", HERE / "decision_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class DecisionContractTests(unittest.TestCase):
    def setUp(self):
        self.request = {"model": "vllm-sr/decision", "messages": [{
            "role": "user", "content": json.dumps({
                "state": "Synthetic red card.", "questions": {"color": {
                    "type": "choice", "criteria": {"red": "Red", "blue": "Blue"}
                }}
            })
        }]}
        self.response = {
            "model": "vllm-sr/decision", "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": json.dumps({
                "color": {"choice": "red", "score": 0.9}
            })}, "finish_reason": "stop"}]
        }

    def check(self, response=None):
        return contract.check(self.request, response or self.response)

    def test_typed_choice(self):
        self.assertEqual(self.check(), [])

    def test_native_d1_confidence_field(self):
        self.response["choices"][0]["message"]["content"] = json.dumps({
            "color": {"choice": "red", "confidence": 0.9, "type": "choice"}
        })
        self.assertEqual(self.check(), [])

    def test_live_plain_text_shape_fails(self):
        self.response["choices"][0]["message"]["content"] = "red"
        self.assertTrue(self.check())

    def test_empty_missing_unknown_or_error_answers_fail(self):
        for answers in ({}, {"other": {"choice": "red", "score": 0.9}},
                        {"color": {"error": "backend failed"}},
                        {"color": {"choice": "green", "score": 0.9}}):
            with self.subTest(answers=answers):
                self.response["choices"][0]["message"]["content"] = json.dumps(answers)
                self.assertTrue(self.check())

    def test_scores_must_be_finite_probabilities_not_booleans(self):
        for score in (None, True, "0.9", -1, 2, 10**400, float("nan"), float("inf")):
            with self.subTest(score=score):
                self.response["choices"][0]["message"]["content"] = json.dumps({
                    "color": {"choice": "red", "score": score}
                })
                self.assertTrue(self.check())

    def test_errors_wrong_model_and_partial_completions_fail(self):
        for response in ({"error": "unavailable"}, {}, {"choices": []},
                         {**self.response, "model": "other"}):
            with self.subTest(response=response):
                self.assertTrue(contract.check(self.request, response))
        self.response["choices"][0]["finish_reason"] = "length"
        self.assertTrue(self.check())

    def test_bad_request_fails_closed(self):
        for request in ({}, {"messages": []}, {"messages": [{"role": "user", "content": "text"}]},
                        {"model": "other", "messages": self.request["messages"]}):
            with self.subTest(request=request):
                self.assertTrue(contract.check(request, self.response))

    def test_all_questions_must_be_answered(self):
        request = copy.deepcopy(self.request)
        payload = json.loads(request["messages"][0]["content"])
        payload["questions"]["second"] = payload["questions"]["color"]
        request["messages"][0]["content"] = json.dumps(payload)
        self.assertTrue(contract.check(request, self.response))

    def test_cli_exit_status_and_no_content_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / "request.json"
            response = Path(tmp) / "response.json"
            request.write_text(json.dumps(self.request))
            for content, expected in ((self.response, 0), ({"error": "SENSITIVE_SENTINEL"}, 1)):
                response.write_text(json.dumps(content))
                result = subprocess.run([sys.executable, str(HERE / "decision_contract.py"),
                                         str(request), str(response)], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(json.loads(result.stdout)["typed_choice_contract"], expected == 0)
                self.assertNotIn("SENSITIVE_SENTINEL", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
