"""Objectives shared by the stage 3-5 graph recommenders."""

from __future__ import annotations

import torch
from torch import Tensor, nn
import torch.nn.functional as functional


class BPRLoss(nn.Module):
    """Pairwise ranking loss over sampled *unobserved* candidates.

    A sampled candidate is a training comparison, not evidence of negative user
    feedback. Callers must obtain it from the time-valid candidate protocol.
    """

    def __init__(self, regularization: float = 0.0) -> None:
        super().__init__()
        if regularization < 0:
            raise ValueError("regularization must be non-negative")
        self.regularization = regularization

    def forward(
        self,
        positive_scores: Tensor,
        unobserved_scores: Tensor,
        *regularized_tensors: Tensor,
    ) -> Tensor:
        if positive_scores.shape != unobserved_scores.shape:
            raise ValueError("positive and unobserved scores must have the same shape")
        ranking = -functional.logsigmoid(positive_scores - unobserved_scores).mean()
        if not regularized_tensors or self.regularization == 0:
            return ranking
        penalty = sum(tensor.square().sum() for tensor in regularized_tensors)
        return ranking + self.regularization * penalty / positive_scores.numel()


class MultiGranularLoss(nn.Module):
    """Issue objective plus a project-level auxiliary propagation constraint."""

    def __init__(self, repository_weight: float) -> None:
        super().__init__()
        if repository_weight < 0:
            raise ValueError("repository_weight must be non-negative")
        self.repository_weight = repository_weight

    def forward(self, issue_loss: Tensor, repository_loss: Tensor) -> Tensor:
        return issue_loss + self.repository_weight * repository_loss
