"""TemplateGenerator — grounded, varied openers with no model and no cost.

It picks a "hook" from the strongest signal we actually scraped (so it can't
hallucinate — there's no model to invent anything), fills spintax for variety
so 150 DMs aren't identical (a spam signal), and records which fields it used
so the approve-before-send UI can show *why* it wrote what it wrote.

Voice is selectable via GenerationContext.tone (casual | professional | warm).
Every tone is just a bag of spintax strings in TONES below — edit freely, no
code change needed.

Hook priority (first one with real data wins):
    business + category  ->  topic hashtag  ->  activity style  ->  generic
"""

import random
from typing import List, Optional, Tuple

from .base import GeneratedMessage, GenerationContext, MessageGenerator
from .schema import LeadEnrichment
from .spintax import spin

# --- voice presets -----------------------------------------------------------
# Each tone supplies the same set of blocks. `category_verb` is followed in code
# by "a/an {category}". `{topic}` in `topic` is filled from the lead's hashtag.
TONES = {
    "casual": {
        "greeting_named": "{Hey|Hi|Hello}",
        "greeting_anon": "{Hey there|Hi there|Hello}",
        "category_verb": "{love|really like|admire} your work as",
        "topic": "{loved|really enjoyed|came across} your {content|posts} {around|on} {topic}",
        "video": "your {reels|short-form videos} {caught my eye|are genuinely good|stood out}",
        "carousel": "your {carousels|breakdowns|educational posts} {are really well put together|caught my eye}",
        "photo": "your {feed|photography} {looks great|caught my eye|has a great vibe}",
        "mixed": "{been enjoying|really like} your {recent posts|content lately}",
        "generic": "{came across your profile|been following your work} and {really like it|wanted to reach out}",
        "bridge": "{Wanted to reach out|Thought I'd say hi|Had a quick thought to share}.",
    },
    "professional": {
        "greeting_named": "{Hi|Hello}",
        "greeting_anon": "{Hello|Hi there}",
        "category_verb": "{really impressed by|genuinely admire} your work as",
        "topic": "{impressed by|been following} your {content|work} {on|around} {topic}",
        "video": "your {video content|short-form work} {is excellent|really stands out}",
        "carousel": "your {educational carousels|in-depth posts} {are very well crafted|stood out to me}",
        "photo": "your {visual work|portfolio} {is impressive|really stands out}",
        "mixed": "{consistently impressed by|been following} your {recent work|content}",
        "generic": "{came across your profile|have been following your work} and {was impressed|wanted to connect}",
        "bridge": "{Wanted to connect|Reaching out to introduce myself|Hoping to start a conversation}.",
    },
    "warm": {
        "greeting_named": "{Hey|Hi|Heya}",
        "greeting_anon": "{Hey there|Hi there|Hello}",
        "category_verb": "{absolutely love|am such a fan of} your work as",
        "topic": "{absolutely loved|am obsessed with|so enjoyed} your {content|posts} {around|on} {topic}",
        "video": "your {reels|videos} {are so good|completely caught my eye|made me smile}",
        "carousel": "your {carousels|posts} {are so thoughtfully made|really caught my eye}",
        "photo": "your {feed|photos} {are gorgeous|have such a lovely vibe|made me smile}",
        "mixed": "{been loving|am really enjoying} your {recent posts|content lately}",
        "generic": "{found your profile|stumbled on your work} and {fell in love with it|just had to say hi}",
        "bridge": "{Hope you're having a great week!|Just wanted to say hi.|Had to reach out.}",
    },
}
DEFAULT_TONE = "casual"


class TemplateGenerator(MessageGenerator):
    name = "template"

    def generate(
        self,
        lead: LeadEnrichment,
        context: Optional[GenerationContext] = None,
        attempt: int = 0,
    ) -> GeneratedMessage:
        ctx = context or GenerationContext()
        blocks = TONES.get(ctx.tone, TONES[DEFAULT_TONE])
        # Seed off username + tone + attempt: stable per lead, "regenerate" varies.
        rng = random.Random(f"{lead.username}|{ctx.tone}|{attempt}")

        used: List[str] = []
        if lead.first_name:
            greeting = f"{spin(blocks['greeting_named'], rng)} {lead.first_name}"
            used.append("full_name")
        else:
            greeting = spin(blocks["greeting_anon"], rng)

        hook, hook_fields, grounded = self._hook(lead, rng, blocks)
        used.extend(hook_fields)

        opener = f"{greeting}, {hook}."
        if ctx.tail:
            text = f"{opener} {ctx.tail.strip()}"
        elif grounded:
            # grounded hook is just a hook -> add a soft bridge sentence
            text = f"{opener} {spin(blocks['bridge'], rng)}"
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
    def _hook(self, lead: LeadEnrichment, rng, blocks) -> Tuple[str, List[str], bool]:
        if lead.is_business and lead.clean_category:
            topic = lead.clean_category
            article = "an" if topic[:1] in "aeiou" else "a"
            return f"{spin(blocks['category_verb'], rng)} {article} {topic}", \
                   ["is_business", "category"], True
        if lead.primary_topic:
            return spin(blocks["topic"].replace("{topic}", lead.primary_topic), rng), \
                   ["top_hashtags"], True
        if lead.activity_kind == "video":
            return spin(blocks["video"], rng), ["inferred_activity"], True
        if lead.activity_kind == "carousel":
            return spin(blocks["carousel"], rng), ["inferred_activity"], True
        if lead.activity_kind == "photo":
            return spin(blocks["photo"], rng), ["inferred_activity"], True
        if lead.activity_kind == "mixed":
            return spin(blocks["mixed"], rng), ["inferred_activity"], True
        # Nothing to ground on -> honest generic opener (grounded=False).
        return spin(blocks["generic"], rng), [], False

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
