"""Local retriever for the pet-care knowledge base.

Each markdown file holds one or more notes with a small header (species,
topic, recommended time, and keywords) and a short prose passage. The
retriever scores those notes with keyword coverage plus TF-IDF cosine
similarity. The prose is what the advisor quotes. The header is what it
uses to build a task.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pawpal_ai.guardrails import STOPWORDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
PASSAGE_PATTERN = re.compile(r"^---\n(.*?)\n---\n(.*?)(?=^---\n|\Z)", re.M | re.S)


@dataclass(frozen=True)
class Passage:
    id: str
    species: str
    topic: str
    age_group: str
    recommended_time: str
    duration_minutes: int
    priority: str
    frequency: str
    task_description: str
    keywords: list[str]
    text: str
    source: str

    @property
    def index_text(self) -> str:
        keyword_text = " ".join(self.keywords)
        return " ".join(
            part
            for part in (
                self.species,
                self.topic,
                self.age_group,
                self.task_description,
                keyword_text,
                self.text,
            )
            if part
        )

    @property
    def label_tokens(self) -> set[str]:
        raw = " ".join(
            (
                self.species,
                self.topic,
                self.age_group,
                self.task_description,
                " ".join(self.keywords),
            )
        )
        return set(tokenize(raw))


@dataclass(frozen=True)
class RetrievedPassage:
    passage: Passage
    score: float


def default_knowledge_dir() -> Path:
    return PROJECT_ROOT / "knowledge"


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def content_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOPWORDS}


def _parse_frontmatter(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def load_passages(knowledge_dir: Path) -> tuple[list[Passage], list[str]]:
    """Load every note. Returns passages and human-readable warnings."""
    if not knowledge_dir.is_dir():
        raise FileNotFoundError(
            f"Knowledge directory not found: {knowledge_dir}. "
            "Run PawPal from the project root so knowledge/ is available."
        )
    passages: list[Passage] = []
    warnings: list[str] = []
    files = sorted(knowledge_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No markdown notes found in {knowledge_dir}.")

    for path in files:
        raw = path.read_text(encoding="utf-8")
        matches = list(PASSAGE_PATTERN.finditer(raw))
        if not matches:
            warnings.append(f"Skipped {path.name}: no notes with a header.")
            continue
        source = f"knowledge/{path.name}"
        for match in matches:
            fields = _parse_frontmatter(match.group(1))
            note_id = fields.get("id", "")
            if not note_id:
                warnings.append(f"Skipped a note in {path.name}: missing id.")
                continue
            try:
                duration = int(fields.get("duration_minutes") or 0)
            except ValueError:
                warnings.append(f"Skipped {note_id}: duration is not a number.")
                continue
            keywords = [
                part.strip()
                for part in fields.get("keywords", "").split(",")
                if part.strip()
            ]
            passages.append(
                Passage(
                    id=note_id,
                    species=fields.get("species", "all").lower(),
                    topic=fields.get("topic", "").lower(),
                    age_group=fields.get("age_group", "any").lower() or "any",
                    recommended_time=fields.get("recommended_time", ""),
                    duration_minutes=duration,
                    priority=fields.get("priority", "medium").lower() or "medium",
                    frequency=fields.get("frequency", "daily").lower() or "daily",
                    task_description=fields.get("task_description", ""),
                    keywords=keywords,
                    text=match.group(2).strip(),
                    source=source,
                )
            )
    if not passages:
        raise ValueError(f"No usable notes in {knowledge_dir}.")
    return passages, warnings


class KnowledgeRetriever:
    """Rank care notes for a query, optionally limited to one species."""

    def __init__(self, knowledge_dir: Path | None = None):
        self.knowledge_dir = knowledge_dir or default_knowledge_dir()
        self.passages, self.warnings = load_passages(self.knowledge_dir)
        self._idf = self._build_idf()

    def _build_idf(self) -> dict[str, float]:
        documents = [tokenize(passage.index_text) for passage in self.passages]
        document_count = len(documents)
        frequencies: Counter[str] = Counter()
        for tokens in documents:
            frequencies.update(set(tokens))
        return {
            token: math.log((1 + document_count) / (1 + count)) + 1.0
            for token, count in frequencies.items()
        }

    def retrieve(
        self,
        query: str,
        species: str | None = None,
        age_group: str | None = None,
        k: int = 3,
    ) -> list[RetrievedPassage]:
        """Return the top-k notes for this query, highest score first."""
        candidates = self.passages
        if species:
            species = species.lower()
            candidates = [
                passage
                for passage in candidates
                if passage.species in (species, "all")
            ]
        query_tokens = [token for token in tokenize(query) if token not in STOPWORDS]
        query_tf = self._term_frequency(query_tokens)
        wanted = content_tokens(query)
        ranked: list[RetrievedPassage] = []
        for passage in candidates:
            score = self._score(passage, query_tf, wanted, age_group)
            ranked.append(RetrievedPassage(passage=passage, score=round(score, 4)))
        ranked.sort(key=lambda hit: (-hit.score, hit.passage.id))
        return ranked[:k]

    def _score(
        self,
        passage: Passage,
        query_tf: dict[str, float],
        wanted: set[str],
        age_group: str | None,
    ) -> float:
        if wanted:
            overlap = len(wanted & passage.label_tokens) / len(wanted)
        else:
            overlap = 0.0
        cosine = self._cosine(query_tf, self._term_frequency(tokenize(passage.index_text)))
        base = 0.65 * overlap + 0.35 * cosine
        return min(1.0, base * self._age_factor(passage.age_group, age_group))

    def _age_factor(self, passage_age: str, pet_age: str | None) -> float:
        if pet_age is None or passage_age == "any" or passage_age == pet_age:
            return 1.0
        if pet_age == "young" and passage_age == "adult":
            return 0.9
        return 0.55

    @staticmethod
    def _term_frequency(tokens: list[str]) -> dict[str, float]:
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = float(len(tokens))
        return {token: count / total for token, count in counts.items()}

    def _cosine(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        dot = left_norm = right_norm = 0.0
        for token in set(left) | set(right):
            idf = self._idf.get(token, 1.0)
            left_value = left.get(token, 0.0) * idf
            right_value = right.get(token, 0.0) * idf
            dot += left_value * right_value
            left_norm += left_value * left_value
            right_norm += right_value * right_value
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / math.sqrt(left_norm * right_norm)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rank PawPal care notes for a query.")
    parser.add_argument("--species", default=None, help="Keep notes for this species, plus general notes.")
    parser.add_argument("--age", dest="age_group", default=None, help="young, adult, or senior.")
    parser.add_argument("query", nargs="+", help="The care question to rank.")
    args = parser.parse_args()
    retriever = KnowledgeRetriever()
    question = " ".join(args.query)
    print(f"Query: {question}")
    if args.species:
        print(f"Species filter: {args.species}")
    hits = retriever.retrieve(
        question,
        species=args.species,
        age_group=args.age_group,
        k=5,
    )
    for hit in hits:
        print(f"  {hit.score:.4f}  {hit.passage.id}  ({hit.passage.source})")
