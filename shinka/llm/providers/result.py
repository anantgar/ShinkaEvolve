from typing import Dict, List, Optional


class QueryResult:
    def __init__(
        self,
        msg: str,
        system_msg: str,
        model_name: str,
        kwargs: Dict,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
        cost: float | None = 0.0,
        input_cost: float | None = 0.0,
        output_cost: float | None = 0.0,
        content: str = "",
        new_msg_history: Optional[List[Dict]] = None,
        thought: str = "",
        model_posteriors: Optional[Dict[str, float]] = None,
        num_tool_calls: int = 0,
        num_total_queries: int = 1,
    ):
        self.msg = msg
        self.system_msg = system_msg
        self.model_name = model_name
        self.kwargs = kwargs
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.thinking_tokens = thinking_tokens
        self.cost = cost
        self.input_cost = input_cost
        self.output_cost = output_cost
        self.content = content
        self.new_msg_history = new_msg_history if new_msg_history is not None else []
        self.thought = thought
        self.model_posteriors = model_posteriors or {}
        self.num_tool_calls = num_tool_calls
        self.num_total_queries = num_total_queries

    def to_dict(self):
        return {
            "msg": self.msg,
            "system_msg": self.system_msg,
            "model_name": self.model_name,
            "kwargs": self.kwargs,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cost": self.cost,
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "content": self.content,
            "new_msg_history": self.new_msg_history,
            "thought": self.thought,
            "model_posteriors": self.model_posteriors,
            "num_tool_calls": self.num_tool_calls,
            "num_total_queries": self.num_total_queries,
        }

    def __str__(self):
        """Return string representation of query result."""
        lines = []
        lines.append("=" * 80)
        lines.append(f"Model: {self.model_name}")
        total_cost = "unknown" if self.cost is None else f"${self.cost:.4f}"
        input_cost = "unknown" if self.input_cost is None else f"${self.input_cost:.4f}"
        output_cost = (
            "unknown" if self.output_cost is None else f"${self.output_cost:.4f}"
        )
        lines.append(f"Total Cost: {total_cost}")
        lines.append(f"  Input: {input_cost} ({self.input_tokens} tokens)")
        lines.append(f"  Output: {output_cost} ({self.output_tokens} tokens)")
        thinking_ratio = (
            self.thinking_tokens / self.output_tokens if self.output_tokens else 0.0
        )
        lines.append(
            f"  --> Thinking tokens: {self.thinking_tokens} ({thinking_ratio:.2f})"
        )
        if self.thinking_tokens > 0:
            lines.append(f"  Thinking: {self.thinking_tokens} tokens")
        lines.append("-" * 80)
        if self.thought:
            lines.append("Thought:")
            lines.append(self.thought)
            lines.append("-" * 80)
        if self.model_posteriors:
            lines.append("-" * 80)
            lines.append("Model Posteriors:")
            for model, prob in self.model_posteriors.items():
                lines.append(f"  {model}: {prob:.4f}")
        if self.num_total_queries > 0:
            lines.append("-" * 80)
            lines.append(f"Number of Total Queries: {self.num_total_queries}")
        if self.num_tool_calls > 0:
            lines.append("-" * 80)
            lines.append(f"Number of Tool Calls: {self.num_tool_calls}")
        lines.append("=" * 80)
        return "\n".join(lines)
