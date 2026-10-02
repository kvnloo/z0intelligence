"""Public fixture wire capture with a create-only one-call fuse; no headers saved."""
import json
import os
from pathlib import Path


def install(worker, output: Path) -> None:
    base = worker.OpenAICompatTransport

    class StudyTransport(base):
        def _post(self, path, payload):
            # A study-wide fuse survives output-directory changes and uncertain calls.
            # A launch consumes the slot before transport. It is never auto-reset.
            fuse = Path(__file__).resolve().parents[2] / "physical-call-consumed.v1.json"
            fd = os.open(fuse, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"study_id": "aodl-nous-solar-completion-v1", "output": str(output), "physical_call_cap": 1}, handle)
            request = {"endpoint": self.config.url(path), "payload": payload}
            (output / "public-request.json").write_text(json.dumps(request, sort_keys=True, indent=2) + "\n")
            try:
                response = super()._post(path, payload)
            except Exception as exc:
                (output / "transport-status.json").write_text(json.dumps({"response_received": False, "error_type": type(exc).__name__, "usage": None}) + "\n")
                raise
            (output / "public-response.json").write_text(json.dumps(response, sort_keys=True, indent=2) + "\n")
            return response

    worker.OpenAICompatTransport = StudyTransport
