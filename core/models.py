"""数据模型：题目与判题结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Puzzle:
    id: str
    title: str
    story: str
    answer: str
    hints: list[str] = field(default_factory=list)
    category: str = ""
    difficulty: str = "medium"
    core_facts: list[str] = field(default_factory=list)
    source: str = ""
    source_url: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Puzzle:
        return cls(
            id=row["id"],
            title=row["title"],
            story=row["story"],
            answer=row["answer"],
            hints=list(row["hints"] or []),
            category=row["category"] or "",
            difficulty=row["difficulty"] or "medium",
            core_facts=list(row["core_facts"] or []),
            source=row["source"] or "",
            source_url=row["source_url"] or "",
        )

    def to_dict(self, with_answer: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "story": self.story,
            "hints": self.hints,
            "category": self.category,
            "difficulty": self.difficulty,
            "source": self.source,
            "source_url": self.source_url,
        }
        if with_answer:
            data["answer"] = self.answer
        return data
