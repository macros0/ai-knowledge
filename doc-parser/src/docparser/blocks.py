from dataclasses import dataclass, field


@dataclass
class Block:
    type: str  # heading | paragraph | table | code | comment | attachment | image
    text: str
    level: int | None = None
    meta: dict = field(default_factory=dict)
