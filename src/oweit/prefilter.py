"""Cheap regex gate: only my messages that sound like a commitment go to Claude.

Recall-first on purpose: Claude throws out the false positives, but anything this drops is lost.
"""

import re

_VERBS = (
    "do|send|share|call|ping|text|mail|email|check|get|finish|submit|bring|pay|return|fix|update"
    "|review|look|try|reach|come|join|complete|upload|forward|confirm|book|buy|drop|pick|tell"
    "|let|ask|reply|arrange|sort|handle|make|write|print|transfer|be|attend|take|cover"
)

COMMITMENT = re.compile(
    r"|".join(
        [
            # English
            r"\bi\s*(?:'|’)?\s*ll\b",  # I'll, Ill, i 'll
            r"\bi\s+(?:will|shall|wil)\b",
            r"\bi\s*(?:'|’)?\s*m\s+(?:going\s+to|gonna)\b",
            r"\bgonna\b",
            rf"\bwill\s+(?:{_VERBS})\b",  # "Will send by tonight"
            r"\blet\s+me\b",
            r"\bi\s+can\b.*\b(?:by|before|tomorrow|tonight|today|tmrw|tmr|monday|tuesday|wednesday"
            r"|thursday|friday|saturday|sunday|weekend|morning|evening)\b",
            rf"\bi\s+can\s+(?:{_VERBS})\b",  # "sure, I can take it"
            r"\bconfirmed\b",  # accepting a meeting / slot
            r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+works\b",  # "4 works", "5:30 pm works"
            r"^\W*(?:\w+\W+)?done\W*$",  # a bare "done 👍" / "yep, done" reply = will do
            r"\bon\s+it\b",
            r"\bpromise\b",
            r"\bconsider\s+it\s+done\b",
            r"\bcount\s+me\s+in\b",
            # Hinglish future forms: karunga, bhej dungi, denge, aa jaunga, kar lenge
            r"\b\w+(?:unga|ungi|oonga|oongi|enge|engi)\b",
            # habitual-as-future: "abhi bhejta hu", "kal karti hoon"
            r"\b\w+(?:ta|ti)\s+(?:hu|hoon|hun|hu+n)\b",
        ]
    ),
    re.I,
)


def is_candidate(text: str) -> bool:
    return bool(COMMITMENT.search(text))
