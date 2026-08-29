import random
from typing import Any, Dict, List, Optional

import numpy as np

from shinka.database import Program
from .prompts_base import perf_str


CROSS_SYS_FORMAT = """
You are given multiple code scripts implementing the same algorithm.
You are tasked with generating a new code snippet that combines these code scripts in a way that is more efficient. 
I.e. perform crossover between the code scripts.
Provide the complete new program code.
You MUST respond using a short summary name, description, and the full code:

<NAME>
A shortened name summarizing the code you are proposing. Lowercase, no spaces, underscores allowed.
</NAME>

<DESCRIPTION>
A description and argumentation process of the code you are proposing.
</DESCRIPTION>

<CODE>
```{language}
# The new rewritten program here.
```
</CODE>

* Keep the markers "EVOLVE-BLOCK-START" and "EVOLVE-BLOCK-END" in the code. Do not change the code outside of these markers.
* Make sure your rewritten program maintains the same inputs and outputs as the original program, but with improved internal implementation.
* Make sure the file still runs after your changes.
* Use the <NAME>, <DESCRIPTION>, and <CODE> delimiters to structure your response. It will be parsed afterwards.
""".rstrip()


CROSS_ITER_MSG = """# Current program

Here is the current program we are trying to improve (you will need to propose a new program with the same inputs and outputs as the original program, but with improved internal implementation):

```{language}
{code_content}
```

Here are the performance metrics of the program:

{performance_metrics}{text_feedback_section}

# Task

Perform a cross-over between the code script above and the one below. Aim to combine the best parts of both code implementations that improves the score.
Provide the complete new program code.

IMPORTANT: Make sure your rewritten program maintains the same inputs and outputs as the original program, but with improved internal implementation.
""".rstrip()


def _embedding_distance(
    parent_embedding: Optional[List[float]],
    inspiration_embedding: Optional[List[float]],
) -> Optional[float]:
    """Return cosine distance, or ``None`` for unusable embeddings."""
    try:
        parent_vector = np.asarray(parent_embedding, dtype=float)
        inspiration_vector = np.asarray(inspiration_embedding, dtype=float)
    except (TypeError, ValueError):
        return None

    if (
        parent_vector.ndim != 1
        or inspiration_vector.ndim != 1
        or parent_vector.shape != inspiration_vector.shape
        or parent_vector.size == 0
        or not np.isfinite(parent_vector).all()
        or not np.isfinite(inspiration_vector).all()
    ):
        return None

    parent_norm = np.linalg.norm(parent_vector)
    inspiration_norm = np.linalg.norm(inspiration_vector)
    if parent_norm == 0 or inspiration_norm == 0:
        return None

    similarity = np.dot(parent_vector, inspiration_vector) / (
        parent_norm * inspiration_norm
    )
    distance = 1.0 - np.clip(similarity, -1.0, 1.0)
    return float(distance) if np.isfinite(distance) else None


def _select_inspiration(
    parent: Optional[Program],
    inspirations: List[Program],
    sources: List[str],
    selection_policy: str,
) -> tuple[Program, Dict[str, Any]]:
    """Select one inspiration and return audit metadata for the decision."""
    if not inspirations:
        raise ValueError("At least one crossover inspiration is required")
    if len(inspirations) != len(sources):
        raise ValueError("Each crossover inspiration must have a source")
    if selection_policy not in {"random", "embedding_distance"}:
        raise ValueError(
            "crossover inspiration selection must be 'random' or 'embedding_distance'"
        )

    parent_embedding = parent.embedding if parent is not None else None
    distances = [
        _embedding_distance(parent_embedding, inspiration.embedding)
        for inspiration in inspirations
    ]
    usable_indices = [
        index for index, distance in enumerate(distances) if distance is not None
    ]
    # Consume the same control draw in both arms so choosing by distance does not
    # shift the global RNG stream for later parent/patch decisions.
    random_draw = random.choice(inspirations)
    random_draw_index = next(
        index
        for index, inspiration in enumerate(inspirations)
        if inspiration is random_draw
    )

    fallback_reason: Optional[str] = None
    effective_policy = selection_policy
    if selection_policy == "embedding_distance" and usable_indices:
        selected_index = max(usable_indices, key=lambda index: distances[index])
        selected = inspirations[selected_index]
    else:
        if selection_policy == "embedding_distance":
            effective_policy = "random"
            fallback_reason = "no_usable_embedding_distances"
        selected = random_draw
        selected_index = random_draw_index

    metadata: Dict[str, Any] = {
        "parent_id": parent.id if parent is not None else None,
        "requested_policy": selection_policy,
        "effective_policy": effective_policy,
        "distance_metric": "cosine",
        "fallback_reason": fallback_reason,
        "usable_embedding_count": len(usable_indices),
        "candidate_count": len(inspirations),
        "selected_inspiration_id": selected.id,
        "selected_source": sources[selected_index],
        "selected_distance": distances[selected_index],
        "random_draw_inspiration_id": random_draw.id,
        "random_draw_source": sources[random_draw_index],
        "random_draw_distance": distances[random_draw_index],
        "candidates": [
            {
                "id": inspiration.id,
                "source": sources[index],
                "distance": distances[index],
            }
            for index, inspiration in enumerate(inspirations)
        ],
    }
    return selected, metadata


def get_cross_component(
    archive_inspirations: List[Program],
    top_k_inspirations: List[Program],
    language: str = "python",
    *,
    parent: Optional[Program] = None,
    selection_policy: str = "random",
    selection_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    all_inspirations = archive_inspirations + top_k_inspirations
    sources = ["archive"] * len(archive_inspirations) + ["top_k"] * len(
        top_k_inspirations
    )
    inspiration, metadata = _select_inspiration(
        parent,
        all_inspirations,
        sources,
        selection_policy,
    )
    if selection_metadata is not None:
        selection_metadata.update(metadata)

    crossover_inspiration = "# Crossover Inspiration Programs\n"
    crossover_inspiration += f"```{language}\n{inspiration.code}\n```\n\n"
    crossover_inspiration += f"Performance metrics: {perf_str(inspiration.combined_score, inspiration.public_metrics)}\n\n"

    return crossover_inspiration
