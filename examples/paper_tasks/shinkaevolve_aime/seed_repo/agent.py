"""The single-query agent scaffold used to seed the ShinkaEvolve AIME task."""

from __future__ import annotations

from typing import Callable


class Agent:
    def __init__(
        self, query_llm: Callable[..., tuple[str, float]], temperature: float = 0.0
    ):
        self.output_format_instructions = (
            "On the final line output only the digits of the answer (0‑999). "
            "Provide your final answer enclosed in a LaTeX \\boxed{{...}} command."
        )
        self.query_llm = query_llm
        self.temperature = temperature

    def forward(self, problem: str) -> tuple[str, float]:
        """Query the LLM with one math problem."""
        system_prompt, task_prompt = self.get_prompt_for_task(problem)
        return self.query_llm(
            prompt=task_prompt,
            system=system_prompt,
            temperature=self.temperature,
        )

    def get_prompt_for_task(self, problem: str) -> tuple[str, str]:
        system_prompt = "You are a skilled mathematician."
        task_prompt = f"{self.output_format_instructions}:\n\n{problem}\n\n"
        return system_prompt, task_prompt
