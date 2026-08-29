import pytest
import random

from shinka.database import Program
from shinka.core.sampler import PromptSampler
from shinka.prompts.prompts_cross import get_cross_component


def _program(program_id, embedding):
    return Program(id=program_id, code=program_id, embedding=embedding)


def test_distance_policy_selects_most_distant_inspiration(monkeypatch):
    parent = _program("parent", [1.0, 0.0])
    near = _program("near", [0.99, 0.1])
    far = _program("far", [0.0, 1.0])
    metadata = {}
    # The policy consumes a control random draw to keep both arms' RNG aligned.
    monkeypatch.setattr(
        "shinka.prompts.prompts_cross.random.choice",
        lambda inspirations: inspirations[0],
    )

    component = get_cross_component(
        [near],
        [far],
        parent=parent,
        selection_policy="embedding_distance",
        selection_metadata=metadata,
    )

    assert "far" in component
    assert "near" not in component
    assert metadata["selected_inspiration_id"] == "far"
    assert metadata["selected_source"] == "top_k"
    assert metadata["selected_distance"] == pytest.approx(1.0)
    assert metadata["usable_embedding_count"] == 2
    assert metadata["fallback_reason"] is None
    assert metadata["random_draw_inspiration_id"] == "near"


def test_random_policy_records_candidate_distances(monkeypatch):
    parent = _program("parent", [1.0, 0.0])
    archive = _program("archive", [1.0, 0.0])
    top_k = _program("top-k", [0.0, 1.0])
    metadata = {}
    monkeypatch.setattr(
        "shinka.prompts.prompts_cross.random.choice",
        lambda inspirations: inspirations[0],
    )

    component = get_cross_component(
        [archive],
        [top_k],
        parent=parent,
        selection_policy="random",
        selection_metadata=metadata,
    )

    assert "archive" in component
    assert metadata["requested_policy"] == "random"
    assert metadata["effective_policy"] == "random"
    assert [candidate["distance"] for candidate in metadata["candidates"]] == [
        pytest.approx(0.0),
        pytest.approx(1.0),
    ]


def test_policies_consume_the_same_random_control_draw():
    parent = _program("parent", [1.0, 0.0])
    candidates = [
        _program("near", [1.0, 0.0]),
        _program("far", [0.0, 1.0]),
    ]

    random.seed(42)
    get_cross_component(candidates, [], parent=parent, selection_policy="random")
    after_random_policy = random.random()

    random.seed(42)
    get_cross_component(
        candidates,
        [],
        parent=parent,
        selection_policy="embedding_distance",
    )
    after_distance_policy = random.random()

    assert after_distance_policy == after_random_policy


def test_distance_policy_falls_back_to_random_without_usable_embeddings(monkeypatch):
    parent = _program("parent", [])
    selected = _program("selected", [])
    metadata = {}
    monkeypatch.setattr(
        "shinka.prompts.prompts_cross.random.choice",
        lambda inspirations: selected,
    )

    component = get_cross_component(
        [selected],
        [],
        parent=parent,
        selection_policy="embedding_distance",
        selection_metadata=metadata,
    )

    assert "selected" in component
    assert metadata["effective_policy"] == "random"
    assert metadata["fallback_reason"] == "no_usable_embedding_distances"
    assert metadata["usable_embedding_count"] == 0


@pytest.mark.parametrize(
    "embedding",
    [None, [], [0.0, 0.0], [float("nan"), 1.0], [1.0]],
)
def test_distance_policy_ignores_unusable_candidate_embeddings(embedding):
    parent = _program("parent", [1.0, 0.0])
    unusable = _program("unusable", embedding)
    usable = _program("usable", [0.0, 1.0])
    metadata = {}

    get_cross_component(
        [unusable, usable],
        [],
        parent=parent,
        selection_policy="embedding_distance",
        selection_metadata=metadata,
    )

    assert metadata["selected_inspiration_id"] == "usable"
    assert metadata["usable_embedding_count"] == 1


def test_cross_component_rejects_unknown_selection_policy():
    with pytest.raises(ValueError, match="random.*embedding_distance"):
        get_cross_component(
            [_program("candidate", [1.0])],
            [],
            selection_policy="unknown",
        )


def test_prompt_sampler_exposes_selection_metadata():
    parent = _program("parent", [1.0, 0.0])
    near = _program("near", [1.0, 0.0])
    far = _program("far", [0.0, 1.0])
    metadata = {}
    sampler = PromptSampler(
        patch_types=["cross"],
        patch_type_probs=[1.0],
        crossover_inspiration_selection="embedding_distance",
    )

    _, _, patch_type = sampler.sample(
        parent,
        [near],
        [far],
        selection_metadata=metadata,
    )

    assert patch_type == "cross"
    assert metadata["selected_inspiration_id"] == "far"
