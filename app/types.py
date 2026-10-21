from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ParsedQuery:
    """What an interpreter believes the shopper meant. Every engine produces one of these."""
    year: int | None = None
    vehicles: list[tuple[str, str]] = field(default_factory=list)  # (make, model); >1 = ambiguous
    partnames: list[str] = field(default_factory=list)
    keywords: str = ""
    sku: str | None = None
    confidence: float | None = None  # overall, 0-1
    field_confidence: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["vehicles"] = [list(v) for v in self.vehicles]
        return d


@dataclass
class InterpretResult:
    parsed: ParsedQuery
    engine: str
    network_ms: float = 0.0  # wall clock around the vendor call (0 for code-only engines)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    request_id: str | None = None
    fast_path: bool = False  # True when the model call was skipped
    error: str | None = None
    raw: dict | None = None
    relevance: dict[str, float] = field(default_factory=dict)  # sku -> 0..1, scored in the same AI call
