from dataclasses import dataclass, field, asdict

SAFE = "SAFE"
REVIEW = "REVIEW"
INFO = "INFO"


@dataclass
class Finding:
    id: str
    category: str
    title: str
    path: str
    size: int
    reason: str
    risk: str
    action: dict = field(default_factory=dict)
    needs_admin: bool = False
    selectable: bool = True
    note: str = ""

    def to_dict(self):
        return asdict(self)
