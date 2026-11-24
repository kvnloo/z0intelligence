#!/usr/bin/env python3
"""Refuse any Google/Gemini route or key in the router deploy (owner rule, 2026-11-03).

Owner: "router shouldn't have access to any gemini keys ever. also make it so that no llm can
get access or set it up." Gemini is used only through the agy CLI (Google ban risk).

Checks the router config and deploy.sh as text, so a new provider type, base URL, model id,
recipe or --allow flag that names Google fails here before helm runs. Exit 1 lists each hit.
Usage: no_google_guard.py config.yaml [deploy.sh ...]
"""
import re
import sys

BANNED = re.compile(
    r"gemini|google|generativelanguage|aiplatform|vertex|GOOGLE_API_KEY|GEMINI_API_KEY|"
    r"GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_CLOUD|palm|bard",
    re.IGNORECASE,
)
# Comments that explain the ban are allowed; everything else is not.
ALLOWED_LINE = re.compile(r"^\s*#.*(no[_ -]?google|banned|never|owner rule)", re.IGNORECASE)


def main(paths: list[str]) -> int:
    hits = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if BANNED.search(line) and not ALLOWED_LINE.search(line) and "no_google_guard" not in line:
                    hits.append(f"{path}:{n}: {line.strip()[:160]}")
    if hits:
        print("no-google guard: Google/Gemini is banned from the router (Gemini only via agy CLI):", file=sys.stderr)
        print("\n".join(hits), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["config.yaml", "deploy.sh"]))
