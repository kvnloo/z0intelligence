"""Independent fixture oracle checker. Never imported by the context renderer."""
import json


def grade(answer, expected, visible_source_ids):
    # Serialized comparison deliberately distinguishes true from 1 and false from 0.
    encode = lambda value: json.dumps(value, sort_keys=True, allow_nan=False)
    try:
        matches = encode(answer) == encode(expected)
        visible = all(citation in visible_source_ids
                      for part in answer.values() for citation in part["citations"])
    except (TypeError, ValueError, KeyError, AttributeError):
        return {"passed": False, "exact_fixture_answer": False, "citations_visible": False}
    return {"passed": matches and visible,
            "exact_fixture_answer": matches, "citations_visible": visible}
