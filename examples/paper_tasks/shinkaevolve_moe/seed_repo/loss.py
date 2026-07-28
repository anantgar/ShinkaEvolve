"""Global-batch MoE load-balancing loss used to seed ShinkaEvolve."""

from __future__ import annotations


def load_balancing_loss(
    gate_logits,
    num_experts: int,
    top_k: int = 2,
    attention_mask=None,
):
    """Compute the paper's global-batch auxiliary router loss."""
    import torch
    import torch.nn.functional as functional

    if not gate_logits:
        raise ValueError("gate_logits must contain at least one layer")
    first = gate_logits[0]
    if first.shape[-1] != num_experts:
        raise ValueError("last gate-logit dimension must equal num_experts")
    if attention_mask is None:
        token_count = first.numel() // num_experts
        valid_tokens = None
    else:
        token_count = attention_mask.numel()
        valid_tokens = attention_mask.reshape(token_count).to(first.device, first.dtype)

    logits = torch.stack(
        [layer.reshape(token_count, num_experts) for layer in gate_logits],
        dim=1,
    )
    routing_probabilities = torch.softmax(logits, dim=-1)
    selected = torch.topk(routing_probabilities, top_k, dim=-1).indices
    selection_mask = functional.one_hot(selected, num_classes=num_experts).to(
        routing_probabilities.dtype
    )

    if valid_tokens is None:
        mean_selection = selection_mask.mean(dim=0)
        mean_probability = routing_probabilities.mean(dim=0)
    else:
        selection_weights = valid_tokens[:, None, None, None]
        probability_weights = valid_tokens[:, None, None]
        denominator = valid_tokens.sum().clamp_min(1.0)
        mean_selection = (selection_mask * selection_weights).sum(dim=0) / denominator
        mean_probability = (routing_probabilities * probability_weights).sum(
            dim=0
        ) / denominator

    per_layer = mean_selection * mean_probability.unsqueeze(-2)
    return per_layer.mean(dim=0).sum() * num_experts
