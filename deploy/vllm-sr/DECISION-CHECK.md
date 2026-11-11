# Check captured decision responses

Run this offline check from the repository root:

```sh
python3 deploy/vllm-sr/decision_contract.py request.json response.json
```

Use an OpenAI chat request with `model: vllm-sr/decision`. Its last user message contains JSON with a `questions` dictionary. This check supports `type: choice` questions with a nonempty `criteria` dictionary.

The response must contain one completed assistant message. Its JSON content maps every question ID to a `choice` from that question's criteria and a finite `confidence` or `score` between zero and one. See [`decision_contract.py`](decision_contract.py) and [`test_decision_contract.py`](test_decision_contract.py).

Exit 0 means that the captured response meets this typed choice contract. Exit 1 means that the capture fails the contract or cannot be read. The summary contains reason codes, not request content or upstream error messages.

This check does not call a service, read credentials, change routing, or load a model. It does not prove native `system_one` inference, latency, accuracy, Jev pairing, or a harness alias. A generated JSON answer can meet the wire contract without proving those properties.

Run the offline tests through the fleet:

```sh
fleet-build --light --node mbp --mem 1G --cpus 1 --wait 1800 \
  --src-include deploy/vllm-sr/decision_contract.py \
  --src-include deploy/vllm-sr/test_decision_contract.py \
  -- python3 -m unittest discover -s deploy/vllm-sr -p test_decision_contract.py
```

Never use a model listing or HTTP 200 alone as evidence that typed decisions work.
