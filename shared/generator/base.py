"""MessageGenerator — the interface every opener generator implements.

Today there's one implementation (TemplateGenerator, zero-cost/zero-dep).
Tomorrow a LocalLLM or HostedFreeLLM generator slots in behind the SAME
interface, so the campaign engine never changes and we're never locked to a
provider. The template tier always stays as the offline fallback.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from .schema import LeadEnrichment


@dataclass
class GenerationContext:
    """Per-campaign knobs passed at generation time (not per-lead)."""

    sender_name: str = ""
    tail: str = ""            # the campaign's actual ask, appended after the hook
    tone: str = "casual"     # casual | professional | warm (see TemplateGenerator.TONES)
    max_chars: int = 280     # hard ceiling on the produced opener


@dataclass
class GeneratedMessage:
    """Result of generating one opener — text plus an audit trail."""

    text: str
    generator: str                       # "template" | "local-llm" | ...
    used_fields: List[str] = field(default_factory=list)  # which scraped fields grounded it
    seed: int = 0                        # bump to regenerate a different variant
    grounded: bool = True                # False => fell back to a generic opener

    def explain(self) -> str:
        src = ", ".join(self.used_fields) if self.used_fields else "none (generic)"
        return f"[{self.generator}] grounded_on={src} seed={self.seed}"


class MessageGenerator(ABC):
    """Produce a personalized opener for a single scraped lead."""

    name: str = "abstract"

    @abstractmethod
    def generate(
        self,
        lead: LeadEnrichment,
        context: Optional[GenerationContext] = None,
        attempt: int = 0,
    ) -> GeneratedMessage:
        """Return one opener. `attempt` is the regenerate counter (varies output)."""
        raise NotImplementedError
