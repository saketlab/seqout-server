"""Boolean search queries -> Postgres to_tsquery strings.

Lets power users write   ("aging" OR "aged") (gut OR colon) immun*
and hit exactly those terms, with NO Neo4j ontology expansion. We translate the
google-lite syntax to a to_tsquery('english', ...) string; Postgres still stems
each lexeme, so it matches the same english-stemmed tsv columns the normal path
uses. Malformed input (unbalanced parens, bare operators) is left for
to_tsquery itself to reject -> the caller maps that to a 400.
"""

import re

# Uppercase-only boolean keywords, so a natural lowercase "colon or gut" does
# NOT flip an ordinary query into structured mode.
_BOOL = {"OR": "|", "AND": "&", "NOT": "!"}

# Structured iff the query groups (), quotes a "term", uses a * wildcard, or
# uses a standalone uppercase OR/AND/NOT. Lone lowercase words never trigger.
_TRIGGER = re.compile(r'[()"*]|\b(?:OR|AND|NOT)\b')

# A token is a quoted phrase, a paren, or a bare run without whitespace/parens/quotes.
_TOKEN = re.compile(r'"[^"]*"|[()]|[^\s()"]+')


def is_boolean_query(q: str) -> bool:
    return bool(_TRIGGER.search(q))


def _lexemes(term: str) -> str:
    """One bare word or quoted phrase -> a to_tsquery operand.

    Split on non-alphanumerics into lexemes joined by <-> (phrase/adjacency),
    keeping a trailing * as a :* prefix match on the last lexeme.
    """
    star = term.endswith("*")
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", term) if p]
    if not parts:
        return ""
    if star:
        parts[-1] += ":*"
    return " <-> ".join(parts)


def to_tsquery_string(q: str) -> str:
    """Translate a google-lite boolean query to a to_tsquery('english', ...) input.

    Adjacent operands get an implicit & (AND). Returns "" if nothing usable is
    left (e.g. a lone "*"), which the caller treats as "not really structured".
    """
    out: list[str] = []
    prev_operand = False  # last emitted token closes an operand: word / phrase / )
    for raw in _TOKEN.findall(q):
        if raw in ("(", ")"):
            if raw == "(" and prev_operand:
                out.append("&")
            out.append(raw)
            prev_operand = raw == ")"
            continue
        kw = _BOOL.get(raw)
        if kw == "!":  # NOT is a prefix op; a preceding operand needs an explicit &
            if prev_operand:
                out.append("&")
            out.append("!")
            prev_operand = False
            continue
        if kw:  # | or &
            out.append(kw)
            prev_operand = False
            continue
        lex = _lexemes(raw.strip('"'))
        if not lex:
            continue
        if prev_operand:
            out.append("&")
        out.append(lex)
        prev_operand = True
    return " ".join(out)


if __name__ == "__main__":
    assert is_boolean_query('("aging" OR "aged") (gut OR colon)')
    assert is_boolean_query("immun*")
    assert is_boolean_query("TP53 OR BRCA1")
    assert not is_boolean_query("aging gut immune")
    assert not is_boolean_query("colon cancer")  # lone words, lowercase "or"-free

    assert (
        to_tsquery_string('("aging" OR "aged") (gut OR colon) immun*')
        == "( aging | aged ) & ( gut | colon ) & immun:*"
    )
    assert to_tsquery_string("heart NOT mouse") == "heart & ! mouse"
    assert to_tsquery_string("NOT mouse") == "! mouse"
    assert to_tsquery_string('"single cell"') == "single <-> cell"
    assert to_tsquery_string("immun*") == "immun:*"
    assert to_tsquery_string("*") == ""  # nothing usable -> caller falls back
    print("ok")
