"""Resolve the Studio's supported time signatures before planning."""
import re
from unicodedata import normalize

METERS = {"4/4": 4, "3/4": 3, "6/8": 2}
_NAMES = {
    "四四": "4/4", "四分之四": "4/4", "四拍子": "4/4", "4拍子": "4/4",
    "四三": "3/4", "三四": "3/4", "四分之三": "3/4", "三拍子": "3/4", "3拍子": "3/4",
    "八六": "6/8", "六八": "6/8", "八分之六": "6/8",
}
_PATTERN = re.compile(r"(?<!\d)([346]\s*/\s*[48])(?!\d)|"
                      r"(四四|四分之四|四三|三四|四分之三|八六|六八|八分之六)\s*拍|"
                      r"([三四34]\s*拍子)")
_CHANGE = re.compile(r"(?:换|改|切|变)(?:换|变)?(?:成|到|为|回)|(?:switch|change)\s+to", re.I)


def requested_meter(text):
    text = normalize("NFKC", text)
    choices = set()
    for clause in re.split(r"[，,。；;！!？?]", text):
        if re.search(r"别|不要|不必|不改|不用|\b(?:don't|not)\b", clause, re.I):
            continue
        # In “从 4/4 换到 3/4”, the destination is the requested meter.
        change = list(_CHANGE.finditer(clause))
        if change:
            clause = clause[change[-1].end():]
        for match in _PATTERN.finditer(clause):
            token = re.sub(r"\s+", "", next(value for value in match.groups() if value))
            meter = _NAMES.get(token, token)
            if meter in METERS:
                choices.add(meter)
    return next(iter(choices)) if len(choices) == 1 else None


def remaining_meter_clauses(text):
    """Keep separately spoken actions, e.g. “切到 3/4，鼓轻一点”."""
    return "，".join(clause.strip() for clause in re.split(r"[，,。；;！!？?]", text)
                    if clause.strip() and requested_meter(clause) is None)
