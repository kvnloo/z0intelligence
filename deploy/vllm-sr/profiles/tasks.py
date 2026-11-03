"""Deterministic task battery for ranking free models on easy, well-defined, NON-code work.

Every task has an automatic grader, so a model's score is reproducible and needs no judge model.
Families map to router profiles: extract/classify/format (free-quality), reason (free-reasoning),
verify (free-verify), longctx (free-longctx). Owner rule: free models never write code.
"""
import json, random, re

def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None

def _last_number(text):
    nums = re.findall(r"-?\d[\d,]*\.?\d*", text.replace("$", ""))
    return float(nums[-1].replace(",", "")) if nums else None

def _words(text):
    return re.findall(r"[A-Za-z0-9']+", text)

# --- long context haystack (deterministic) -------------------------------------------------
_rng = random.Random(1102)
_CITIES = ["Avalon", "Brightwater", "Cedar Falls", "Dunmore", "Eastport", "Fairhaven", "Glenrock", "Harlow"]
def _haystack(n_paragraphs, needle):
    paras = []
    for i in range(n_paragraphs):
        c = _CITIES[i % len(_CITIES)]
        paras.append(f"Report {i}: The {c} depot logged {_rng.randint(100, 999)} parcels on shift {i % 3 + 1}. "
                     f"Weather was {_rng.choice(['clear', 'rainy', 'windy', 'foggy'])} and the crew of "
                     f"{_rng.randint(4, 19)} finished at {_rng.randint(13, 23)}:{_rng.randint(10, 59)}. "
                     "No incidents were filed and the loading bay passed its routine inspection.")
    paras.insert(int(n_paragraphs * 0.61), needle)
    return "\n".join(paras)

_NEEDLE = "Report X: The Glenrock depot's spare key code was changed to 4719-KESTREL by the night supervisor."
LONG_8K = _haystack(110, _NEEDLE)     # ~8k tokens
LONG_32K = _haystack(440, _NEEDLE)    # ~32k tokens

TASKS = [
    {"id": "extract-invoice", "family": "extract",
     "prompt": "Extract fields from this text as JSON with keys vendor, invoice_number, total_usd (number), due_date "
               "(YYYY-MM-DD). Output only JSON.\n\nText: Hi team, Northwind Traders sent invoice NW-20931 today. "
               "The amount due is $4,312.50 and they need payment by the 14th of December 2026. Thanks, Priya",
     "grade": lambda t: (lambda j: bool(j) and str(j.get("vendor", "")).lower().startswith("northwind")
                         and str(j.get("invoice_number")) == "NW-20931" and abs(float(j.get("total_usd", 0)) - 4312.5) < 0.01
                         and j.get("due_date") == "2026-12-14")(_json(t))},
    {"id": "classify-intents", "family": "classify",
     "prompt": "Label each message with exactly one of: billing, bug, feature_request, praise, cancel. "
               "Output only JSON mapping id to label.\n1: 'The export button crashes the app on Android.'\n"
               "2: 'Why was I charged twice this month?'\n3: 'Love the new dark mode, great work!'\n"
               "4: 'Please close my account effective today.'\n5: 'Could you add a calendar sync with Outlook?'",
     "grade": lambda t: _json(t) == {"1": "bug", "2": "billing", "3": "praise", "4": "cancel", "5": "feature_request"}},
    {"id": "format-bullets", "family": "format",
     "prompt": "Give exactly 3 tips for sleeping better. Format: exactly 3 lines, each starting with '- ', each at "
               "most 8 words. No title, no other text.",
     "grade": lambda t: (lambda ls: len(ls) == 3 and all(l.startswith("- ") and len(_words(l)) <= 8 for l in ls))(
         [l.strip() for l in t.strip().splitlines() if l.strip()])},
    {"id": "summary-facts", "family": "extract",
     "prompt": "Summarize in at most 35 words. Keep the date, the city and the number of attendees.\n\n"
               "The regional volunteer summit took place on 9 March 2026 in Tacoma. Organizers counted 412 "
               "attendees across two days of workshops on disaster preparedness, food bank logistics and youth "
               "mentoring. Next year's summit will rotate to Spokane, and registration opens in the autumn.",
     "grade": lambda t: len(_words(t)) <= 35 and "Tacoma" in t and "412" in t and re.search(r"9\s+March|March\s+9", t) is not None},
    {"id": "math-word", "family": "reason",
     "prompt": "A shop sells pens at $1.75 each or a box of 12 for $18. Maya needs 40 pens and buys as many boxes as "
               "possible, then single pens for the rest. How much does she pay in dollars? End with the number only on the last line.",
     "grade": lambda t: _last_number(t) == 61.0},
    {"id": "weekday", "family": "reason",
     "prompt": "What day of the week is 2026-11-04? Answer with one word.",
     "grade": lambda t: "wednesday" in t.lower() and len(_words(t)) <= 3},
    {"id": "logic-knights", "family": "reason",
     "prompt": "On an island, knights always tell the truth and knaves always lie. A says: 'B is a knave.' "
               "B says: 'A and I are the same kind.' What are A and B? Answer as JSON {\"A\": ..., \"B\": ...} with "
               "values knight or knave, nothing else.",
     "grade": lambda t: _json(t) == {"A": "knight", "B": "knave"}},
    {"id": "sort-dedupe", "family": "format",
     "prompt": "Sort these words alphabetically, remove duplicates (case-insensitive, keep lowercase), and output them "
               "comma-separated on one line, nothing else: Pear, apple, fig, Apple, kiwi, pear, banana, Fig",
     "grade": lambda t: re.sub(r"\s", "", t.strip().strip(".").lower()) == "apple,banana,fig,kiwi,pear"},
    {"id": "verify-claim", "family": "verify",
     "prompt": "Evidence: 'The bridge opened to traffic in June 2019 after a 30-month build; tolls were removed in 2023.'\n"
               "For each claim answer SUPPORTED, REFUTED or NOT_ENOUGH_INFO. Output only JSON mapping id to answer.\n"
               "c1: The bridge opened in 2019.\nc2: Construction took less than two years.\n"
               "c3: The bridge cost $2 billion.\nc4: The bridge still charges tolls.",
     "grade": lambda t: _json(t) == {"c1": "SUPPORTED", "c2": "REFUTED", "c3": "NOT_ENOUGH_INFO", "c4": "REFUTED"}},
    {"id": "verify-answer", "family": "verify",
     "prompt": "Question: 'How many minutes are in 3.5 hours?' A student answered '210'. Another answered '180'. "
               "Which answers are correct? Output only JSON {\"210\": true|false, \"180\": true|false}.",
     "grade": lambda t: _json(t) == {"210": True, "180": False}},
    {"id": "needle-8k", "family": "longctx",
     "prompt": LONG_8K + "\n\nQuestion: What is the Glenrock depot's spare key code? Answer with the code only.",
     "grade": lambda t: "4719-KESTREL" in t.upper()},
    {"id": "needle-32k", "family": "longctx",
     "prompt": LONG_32K + "\n\nQuestion: What is the Glenrock depot's spare key code? Answer with the code only.",
     "grade": lambda t: "4719-KESTREL" in t.upper()},
]

# --- hard tier: separates "can follow" from "reliably right" (added after the first run hit a 100% ceiling) ---
_TABLE = """region,product,units,unit_price
north,widget,12,4.50
south,gadget,7,12.00
north,gadget,3,12.00
east,widget,20,4.50
south,widget,5,4.50
north,widget,8,4.50
east,gizmo,4,30.00
south,gizmo,2,30.00
north,gizmo,1,30.00
east,gadget,9,12.00"""
_THREAD = """From: Lena (Mon 3 Aug 2026): Kickoff for Project Heron moved to Thursday 6 August. Budget stays at $48,000.
From: Omar (Tue 4 Aug): Correction - finance approved a 15% budget increase this morning.
From: Lena (Tue 4 Aug): Thanks. Also, Raj replaces Omar as tech lead from the kickoff onward.
From: Raj (Wed 5 Aug): Kickoff slips one more week, same weekday, venue unchanged (Room 4B).
From: Omar (Wed 5 Aug): Ignore my earlier note: the increase is 10%, not 15%. Finance misread the sheet."""
TASKS += [
    {"id": "hard-table-agg", "family": "reason",
     "prompt": "CSV:\n" + _TABLE + "\n\nWhat is the total revenue (units*unit_price) for region north, excluding gadgets? "
               "End with the number only on the last line.",
     "grade": lambda t: _last_number(t) == 120.0},
    {"id": "hard-thread-state", "family": "extract",
     "prompt": "Email thread:\n" + _THREAD + "\n\nGive the FINAL state as JSON with keys kickoff_date (YYYY-MM-DD), "
               "budget_usd (number), tech_lead, room. Output only JSON.",
     "grade": lambda t: (lambda j: bool(j) and j.get("kickoff_date") == "2026-08-13" and abs(float(j.get("budget_usd", 0)) - 52800) < 0.5
                         and str(j.get("tech_lead", "")).lower() == "raj" and str(j.get("room", "")).upper().replace("ROOM", "").strip() == "4B")(_json(t))},
    {"id": "hard-days-between", "family": "reason",
     "prompt": "How many days are there from 2026-02-14 to 2026-11-02, counting the end date but not the start date? "
               "End with the number only on the last line.",
     "grade": lambda t: _last_number(t) == 261.0},
    {"id": "hard-schedule", "family": "reason",
     "prompt": "Four talks A, B, C, D fill slots 1-4 (one each). C is immediately after A. B is not in slot 1 or 4. "
               "D is before B. Give the unique order as JSON {\"1\":..,\"2\":..,\"3\":..,\"4\":..}, nothing else.",
     "grade": lambda t: _json(t) == {"1": "D", "2": "B", "3": "A", "4": "C"}},
    {"id": "hard-constraints", "family": "format",
     "prompt": "Output a JSON array of exactly 5 distinct English animal names, sorted alphabetically, all lowercase, "
               "none containing the letter 'e', each a single word. Output only the array.",
     "grade": lambda t: (lambda a: isinstance(a, list) and len(a) == 5 and len(set(a)) == 5 and a == sorted(a)
                         and all(isinstance(x, str) and x.isalpha() and x.islower() and "e" not in x for x in a))(
         (lambda m: json.loads(m.group(0)) if m else None)(re.search(r"\[.*\]", t, re.S)))},
    {"id": "hard-count-letters", "family": "reason",
     "prompt": "How many times does the letter 's' appear in the phrase 'mississippi sunsets'? End with the number only on the last line.",
     "grade": lambda t: _last_number(t) == 7.0},
    {"id": "hard-verify-multi", "family": "verify",
     "prompt": "Passage: 'Dana joined in 2021 as an analyst, became team lead two years later, and left in March 2025 for "
               "a startup in Lisbon. Her team grew from 4 to 9 people while she led it.'\nAnswer each with SUPPORTED, "
               "REFUTED or NOT_ENOUGH_INFO as JSON mapping id to answer, nothing else.\n"
               "a: Dana became team lead in 2023.\nb: Dana led the team for over three years.\nc: The startup is in Portugal.\n"
               "d: The team more than doubled while she led it.\ne: Dana was hired as a team lead.\nf: The startup was founded in 2024.",
     "grade": lambda t: _json(t) == {"a": "SUPPORTED", "b": "REFUTED", "c": "SUPPORTED", "d": "SUPPORTED",
                                     "e": "REFUTED", "f": "NOT_ENOUGH_INFO"}},
    {"id": "hard-needle-multi", "family": "longctx",
     "prompt": _haystack(300, "Report Y: The Avalon depot's backup generator serial is GX-5512.").replace(
         "Report 150:", "Report Z: The Harlow depot's backup generator serial is GX-0931.\nReport 150:")
               + "\n\nQuestion: Which two depots have a backup generator serial recorded, and what is the sum of the two "
                 "serial numbers' digits after 'GX-' read as integers (5512 + ...)? End with the sum only on the last line.",
     "grade": lambda t: _last_number(t) == 6443.0},
]
