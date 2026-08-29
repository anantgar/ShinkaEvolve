from dataclasses import dataclass

import pytest

from shinka.core.novelty_judge import NoveltyJudge, parse_novelty_decision
from shinka.database import Program
from shinka.prompts import NOVELTY_SYSTEM_MSG


@dataclass
class DummyResponse:
    content: str
    cost: float = 0.0


class DummyNoveltyLLM:
    def __init__(self, responses=None, raise_on_query=None):
        self.responses = list(responses or [])
        self.raise_on_query = raise_on_query
        self.messages = []

    def get_kwargs(self):
        return {}

    def query(self, msg, system_msg, llm_kwargs):
        self.messages.append(msg)
        if self.raise_on_query is not None:
            raise self.raise_on_query
        if not self.responses:
            return None

        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DummyIslandManager:
    def __init__(self, initialized=True):
        self.initialized = initialized

    def are_all_islands_initialized(self):
        return self.initialized


class DummyDatabase:
    def __init__(
        self,
        similarity_sequences,
        most_similar_program=None,
        island_initialized=True,
    ):
        self._similarity_sequences = list(similarity_sequences)
        self.most_similar_program = most_similar_program
        self.island_manager = DummyIslandManager(initialized=island_initialized)

    def compute_similarity(self, summary_embedding, island_idx):
        if not self._similarity_sequences:
            return []
        if len(self._similarity_sequences) == 1:
            return self._similarity_sequences[0]
        return self._similarity_sequences.pop(0)

    def get_most_similar_program(self, summary_embedding, island_idx):
        return self.most_similar_program


def make_program(program_id="prog-1", island_idx=0, repo_summary=None):
    return Program(
        id=program_id,
        generation=1,
        island_idx=island_idx,
        repo_summary=repo_summary,
    )


def test_should_check_novelty_rejects_missing_prereqs():
    judge = NoveltyJudge()
    database = DummyDatabase([[]])
    parent_program = make_program()

    assert not judge.should_check_novelty([], 1, parent_program, database)
    assert not judge.should_check_novelty([0.1], 0, parent_program, database)
    assert not judge.should_check_novelty([0.1], 1, None, database)


def test_should_check_novelty_requires_initialized_islands():
    judge = NoveltyJudge()
    parent_program = make_program(island_idx=0)

    initialized_db = DummyDatabase([[]], island_initialized=True)
    assert judge.should_check_novelty([0.1], 1, parent_program, initialized_db)

    uninitialized_db = DummyDatabase([[]], island_initialized=False)
    assert not judge.should_check_novelty([0.1], 1, parent_program, uninitialized_db)

    parent_without_island = make_program(island_idx=None)
    assert not judge.should_check_novelty(
        [0.1], 1, parent_without_island, initialized_db
    )


def test_assess_novelty_accepts_when_similarity_is_empty():
    judge = NoveltyJudge(similarity_threshold=0.9, max_novelty_attempts=3)
    database = DummyDatabase([[]])
    parent_program = make_program()

    accepted, metadata = judge.assess_novelty_with_rejection_sampling(
        proposed_summary="# Individual Summary\n\nCandidate summary",
        summary_embedding=[0.1, 0.2],
        parent_program=parent_program,
        database=database,
    )

    assert accepted
    assert metadata["similarity_scores"] == []
    assert metadata["novelty_checks_performed"] == 0
    assert metadata["novelty_total_cost"] == 0.0


def test_assess_novelty_rejects_after_exhausting_attempts():
    judge = NoveltyJudge(similarity_threshold=0.9, max_novelty_attempts=3)
    database = DummyDatabase([[0.95], [0.96], [0.97]])
    parent_program = make_program()

    accepted, metadata = judge.assess_novelty_with_rejection_sampling(
        proposed_summary="# Individual Summary\n\nCandidate summary",
        summary_embedding=[0.3, 0.4],
        parent_program=parent_program,
        database=database,
    )

    assert not accepted
    assert metadata["novelty_checks_performed"] == 0
    assert metadata["novelty_total_cost"] == 0.0
    assert metadata["max_similarity"] == pytest.approx(0.97)


def test_assess_novelty_accepts_high_similarity_when_llm_marks_novel():
    novelty_llm = DummyNoveltyLLM(
        responses=[DummyResponse(content="NOVEL: meaningful redesign", cost=0.12)]
    )
    judge = NoveltyJudge(
        novelty_llm_client=novelty_llm,
        similarity_threshold=0.9,
        max_novelty_attempts=3,
    )

    existing_summary = "# Individual Summary\n\nExisting approach summary"
    most_similar_program = make_program(
        program_id="existing", repo_summary=existing_summary
    )
    database = DummyDatabase(
        similarity_sequences=[[0.99]],
        most_similar_program=most_similar_program,
    )
    parent_program = make_program(program_id="parent")
    proposed_summary = "# Individual Summary\n\nProposed approach summary"

    accepted, metadata = judge.assess_novelty_with_rejection_sampling(
        proposed_summary=proposed_summary,
        summary_embedding=[0.5, 0.6],
        parent_program=parent_program,
        database=database,
    )

    assert accepted
    assert metadata["novelty_checks_performed"] == 1
    assert metadata["novelty_total_cost"] == pytest.approx(0.12)
    assert metadata["novelty_explanation"].startswith("NOVEL")
    assert proposed_summary in novelty_llm.messages[0]
    assert existing_summary in novelty_llm.messages[0]
    assert "def candidate():" not in novelty_llm.messages[0]
    assert "def solve():" not in novelty_llm.messages[0]


def test_check_llm_novelty_handles_empty_response_and_exception():
    similar_program = make_program(program_id="existing")

    empty_llm = DummyNoveltyLLM(responses=[DummyResponse(content=None, cost=0.5)])
    empty_judge = NoveltyJudge(novelty_llm_client=empty_llm)

    is_novel, explanation, cost = empty_judge.check_llm_novelty(
        proposed_summary="# Individual Summary\n\nCandidate summary",
        most_similar_program=similar_program,
    )

    assert not is_novel
    assert "empty" in explanation.lower()
    assert cost == 0.0

    failing_llm = DummyNoveltyLLM(raise_on_query=RuntimeError("network down"))
    failing_judge = NoveltyJudge(novelty_llm_client=failing_llm)

    is_novel, explanation, cost = failing_judge.check_llm_novelty(
        proposed_summary="# Individual Summary\n\nCandidate summary",
        most_similar_program=similar_program,
    )

    assert not is_novel
    assert "network down" in explanation
    assert cost == 0.0


def test_novelty_prompt_rejects_parameter_tweaks_and_insufficient_evidence():
    assert "Parameters, constants, coordinates" in NOVELTY_SYSTEM_MSG
    assert "Numerical continuation" in NOVELTY_SYSTEM_MSG
    assert "different frozen numeric catalog" in NOVELTY_SYSTEM_MSG
    assert "Additive versus multiplicative safety margins" in NOVELTY_SYSTEM_MSG
    assert "fail closed and return **NOT_NOVEL**" in NOVELTY_SYSTEM_MSG


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("NOVEL\n\nDifferent construction family.", True),
        ("**NOVEL**: different construction family", True),
        ("NOT_NOVEL\n\nOnly constants changed.", False),
        ("**NOT_NOVEL**: only constants changed", False),
        ("NOVEL**: malformed markdown", False),
        ("**NOVEL: malformed markdown", False),
        ("NOVELTY is unclear", False),
        ('{"type":"item.completed"}', False),
        ("", False),
    ],
)
def test_parse_novelty_decision_is_explicit_and_fail_closed(content, expected):
    assert parse_novelty_decision(content) is expected
