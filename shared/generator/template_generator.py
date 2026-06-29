"""TemplateGenerator — grounded, varied openers with no model and no cost.

It picks a "hook" from the strongest signal we actually scraped (so it can't
hallucinate — there's no model to invent anything), fills spintax for variety
so 150 DMs aren't identical (a spam signal), and records which fields it used
so the approve-before-send UI can show *why* it wrote what it wrote.

Hook priority (first one with real data wins):
    business + category  ->  topic hashtag  ->  activity style  ->  generic
"""

import random
from typing import List, Optional, Tuple

from .base import GeneratedMessage, GenerationContext, MessageGenerator
from .schema import LeadEnrichment
from .spintax import spin

# --- the block library (all spintax; edit freely, no code change needed) -----
_GREETING_NAMED = "{Hey|Hi|Hello}"
_GREETING_ANON = "{Hey there|Hi there|Hello}"

# Category hook is built in code (needs a/an agreement); this is just the verb part.
_HOOK_CATEGORY_VERB = "{love|really like|admire} your work as"
_HOOK_TOPIC = "{loved|really enjoyed|came across} your {content|posts} {around|on} {topic}"
_HOOK_VIDEO = "your {reels|short-form videos} {caught my eye|are genuinely good|stood out}"
_HOOK_CAROUSEL = "your {carousels|breakdowns|educational posts} {are really well put together|caught my eye}"
_HOOK_PHOTO = "your {feed|photography} {looks great|caught my eye|has a great vibe}"
_HOOK_MIXED = "{been enjoying|really like} your {recent posts|content lately}"
_HOOK_GENERIC = "{came across your profile|been following your work} and {really like it|wanted to reach out}"

_SOFT_BRIDGE = "{Wanted to reach out|Thought I'd say hi|Had a quick thought to share}."


class TemplateGenerator(MessageGenerator):
    name = "template"

    def generate(
        self,
        lead: LeadEnrichment,
        context: Optional[GenerationContext] = None,
        attempt: int = 0,
    ) -> GeneratedMessage:
        ctx = context or GenerationContext()
        # Seed off the username + attempt: same lead => stable, "regenerate" => new.
        rng = random.Random(f"{lead.username}|{attempt}")

        used: List[str] = []
        if lead.first_name:
            greeting = f"{spin(_GREETING_NAMED, rng)} {lead.first_name}"
            used.append("full_name")
        else:
            greeting = spin(_GREETING_ANON, rng)

        hook, hook_fields, grounded = self._hook(lead, rng)
        used.extend(hook_fields)

        opener = f"{greeting}, {hook}."
        if ctx.tail:
            text = f"{opener} {ctx.tail.strip()}"
        elif grounded:
            # grounded hook is just a hook -> add a soft bridge sentence
            text = f"{opener} {spin(_SOFT_BRIDGE, rng)}"
        else:
            # generic hook is already a complete thought -> no bridge (avoids
            # "...wanted to reach out. Wanted to reach out.")
            text = opener

        text = self._finish(text, ctx.max_chars)
        return GeneratedMessage(
            text=text,
            generator=self.name,
            used_fields=used,
            seed=attempt,
            grounded=grounded,
        )

    # -- hook selection: strongest grounded signal first ----------------------
    def _hook(self, lead: LeadEnrichment, rng) -> Tuple[str, List[str], bool]:
        if lead.is_business and lead.clean_category:
            topic = lead.clean_category
            article = "an" if topic[:1] in "aeiou" else "a"
            return f"{spin(_HOOK_CATEGORY_VERB, rng)} {article} {topic}", \
                   ["is_business", "category"], True
        if lead.primary_topic:
            return spin(_HOOK_TOPIC.replace("{topic}", lead.primary_topic), rng), \
                   ["top_hashtags"], True
        if lead.activity_kind == "video":
            return spin(_HOOK_VIDEO, rng), ["inferred_activity"], True
        if lead.activity_kind == "carousel":
            return spin(_HOOK_CAROUSEL, rng), ["inferred_activity"], True
        if lead.activity_kind == "photo":
            return spin(_HOOK_PHOTO, rng), ["inferred_activity"], True
        if lead.activity_kind == "mixed":
            return spin(_HOOK_MIXED, rng), ["inferred_activity"], True
        # Nothing to ground on -> honest generic opener (grounded=False).
        return spin(_HOOK_GENERIC, rng), [], False

    # -- guardrails -----------------------------------------------------------
    @staticmethod
    def _finish(text: str, max_chars: int) -> str:
        # collapse whitespace, fix spacing around punctuation
        text = " ".join(text.split())
        text = text.replace(" ,", ",").replace(" .", ".").replace(",,", ",")
        # capitalize the first alphabetical character
        for i, ch in enumerate(text):
            if ch.isalpha():
                text = text[:i] + ch.upper() + text[i + 1 :]
                break
        # never emit an unresolved placeholder
        assert "{" not in text and "}" not in text and "%" not in text, f"unfilled template: {text}"
        # length ceiling: trim at the last sentence boundary that fits
        if len(text) > max_chars:
            cut = text[:max_chars]
            dot = cut.rfind(".")
            text = (cut[: dot + 1] if dot > 40 else cut).strip()
        return text
