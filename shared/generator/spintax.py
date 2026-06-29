"""Spintax resolver — turns "{a|b|c}" templates into one varied string.

Supports nesting, e.g. "{Hey|Hi} {there|{friend|pal}}". Empty options are
allowed ("{|, }"), which is handy for optional fragments. Resolution is
innermost-first so nested groups collapse correctly.

This is pure stdlib — no third-party dependency — on purpose: the whole
"template tier" must run anywhere with zero install and zero cost.
"""

import re

_GROUP = re.compile(r"\{([^{}]*)\}")  # matches the innermost {...} (no braces inside)


def spin(template: str, rng) -> str:
    """Resolve every {a|b|c} group in `template`, choosing with `rng`.

    Args:
        template: text containing zero or more spintax groups.
        rng: a random.Random instance (pass a seeded one for reproducibility).

    Returns:
        The fully-resolved string with no remaining spintax groups.
    """
    # Bound the loop defensively so a pathological template can't hang.
    for _ in range(10_000):
        match = _GROUP.search(template)
        if not match:
            break
        options = match.group(1).split("|")
        choice = rng.choice(options)
        template = template[: match.start()] + choice + template[match.end() :]
    return template


def variants(template: str):
    """Yield every possible expansion of a spintax template (for testing/QA).

    Useful to audit that no combination produces something embarrassing.
    Only practical for small templates — the count is multiplicative.
    """
    match = _GROUP.search(template)
    if not match:
        yield template
        return
    head, tail = template[: match.start()], template[match.end() :]
    for option in match.group(1).split("|"):
        for rest in variants(head + option + tail):
            yield rest
