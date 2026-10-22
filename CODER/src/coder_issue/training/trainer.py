"""Explicit training primitives; importing this module never starts training."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import time
from typing import Callable

import numpy as np
import torch
from torch import Tensor

from coder_issue.models.coder import CoderIssueModel
from coder_issue.models.lightgcn import LightGCN
from .losses import BPRLoss
from .negative_sampling import CanonicalCandidateSampler, RepositoryCandidateSampler


def seed_training(seed: int, *, deterministic: bool = True) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if deterministic:
        torch.use_deterministic_algorithms(True)


@dataclass(frozen=True)
class EpochResult:
    issue_loss: float
    repository_loss: float | None
    issue_comparison_count: int
    repository_comparison_count: int


@dataclass(frozen=True)
class BatchProgress:
    """One lightweight progress event emitted after a completed optimizer step."""

    batch: int
    total_batches: int
    processed: int
    total: int
    issue_loss: float
    repository_loss: float | None
    batch_seconds: float | None = None
    process_rss_bytes: int | None = None
    available_memory_bytes: int | None = None
    cuda_allocated_bytes: int | None = None
    cuda_peak_allocated_bytes: int | None = None


def _resource_snapshot(device: torch.device) -> tuple[int | None, int | None, int | None, int | None]:
    try:
        import psutil
        rss = psutil.Process().memory_info().rss
        available = psutil.virtual_memory().available
    except ImportError:
        try:
            from .preflight import _memory
            memory = _memory()
            rss = memory["process_rss"]
            available = memory["physical_available"]
        except Exception:
            rss = available = None
    if device.type == "cuda":
        return rss, available, torch.cuda.memory_allocated(device), torch.cuda.max_memory_allocated(device)
    return rss, available, None, None


@dataclass(frozen=True)
class BatchResumeState:
    """Accumulator at a safe boundary immediately after ``optimizer.step()``."""

    next_batch: int = 0
    issue_loss_sum: float = 0.0
    repository_loss_sum: float = 0.0
    processed: int = 0
    repository_count: int = 0


ProgressCallback = Callable[[BatchProgress], None]
CheckpointCallback = Callable[[BatchProgress, BatchResumeState], None]


def _batches(order: Tensor, size: int):
    for start in range(0, len(order), size):
        yield order[start : start + size]


def train_lightgcn_epoch(
    model: LightGCN,
    *,
    optimizer: torch.optim.Optimizer,
    criterion: BPRLoss,
    developer_ids: np.ndarray,
    sampler: CanonicalCandidateSampler,
    epoch: int,
    batch_size: int,
    seed: int,
    device: torch.device,
    progress_callback: ProgressCallback | None = None,
) -> EpochResult:
    model.train()
    sampled = sampler.sample(epoch)
    generator = torch.Generator(device="cpu").manual_seed(seed + epoch)
    order = torch.randperm(len(sampled.query_ids), generator=generator)
    total = 0.0
    processed = 0
    total_batches = math.ceil(len(order) / batch_size)
    for batch_number, indices in enumerate(_batches(order, batch_size), start=1):
        rows = sampled.query_ids[indices.numpy()]
        users = torch.as_tensor(developer_ids[rows], device=device)
        positive = torch.as_tensor(sampled.positive_ids[indices.numpy()], device=device)
        unobserved = torch.as_tensor(sampled.unobserved_ids[indices.numpy()], device=device)
        user_vectors, item_vectors = model.propagated_embeddings()
        positive_scores = (user_vectors[users] * item_vectors[positive]).sum(-1)
        unobserved_scores = (user_vectors[users] * item_vectors[unobserved]).sum(-1)
        loss = criterion(
            positive_scores,
            unobserved_scores,
            model.embedding(users),
            model.embedding(positive + model.config.num_users),
            model.embedding(unobserved + model.config.num_users),
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        total += float(loss.detach()) * len(indices)
        processed += len(indices)
        if progress_callback is not None:
            progress_callback(BatchProgress(
                batch_number, total_batches, processed, len(order), total / processed, None
            ))
    return EpochResult(total / len(order), None, len(order), 0)


def train_coder_epoch(
    model: CoderIssueModel,
    *,
    optimizer: torch.optim.Optimizer,
    criterion: BPRLoss,
    developer_ids: np.ndarray,
    issue_sampler: CanonicalCandidateSampler,
    repository_sampler: RepositoryCandidateSampler | None,
    repository_weight: float,
    epoch: int,
    batch_size: int,
    seed: int,
    device: torch.device,
    progress_callback: ProgressCallback | None = None,
    checkpoint_callback: CheckpointCallback | None = None,
    resume_state: BatchResumeState | None = None,
    stop_requested: Callable[[], bool] | None = None,
    max_batches: int | None = None,
) -> EpochResult:
    model.train()
    issue_sample = issue_sampler.sample(epoch)
    repository_by_query: dict[int, tuple[int, int]] = {}
    if model.repository_graph is not None and repository_sampler is not None:
        repository_sample = repository_sampler.sample(epoch)
        repository_by_query = {
            int(query): (int(positive), int(unobserved))
            for query, positive, unobserved in zip(
                repository_sample.query_ids,
                repository_sample.positive_ids,
                repository_sample.unobserved_ids,
                strict=True,
            )
        }
    generator = torch.Generator(device="cpu").manual_seed(seed + epoch)
    order = torch.randperm(len(issue_sample.query_ids), generator=generator)
    if max_batches is not None:
        if max_batches <= 0:
            raise ValueError("max_batches must be positive")
        order = order[: max_batches * batch_size]
    resume_state = resume_state or BatchResumeState()
    issue_total = float(resume_state.issue_loss_sum)
    repository_total = float(resume_state.repository_loss_sum)
    repository_count = int(resume_state.repository_count)
    processed = int(resume_state.processed)
    total_batches = math.ceil(len(order) / batch_size)
    for batch_number, indices in enumerate(_batches(order, batch_size), start=1):
        if batch_number <= resume_state.next_batch:
            continue
        batch_started = time.perf_counter()
        positions = indices.numpy()
        rows = issue_sample.query_ids[positions]
        users = torch.as_tensor(developer_ids[rows], device=device)
        positive = torch.as_tensor(issue_sample.positive_ids[positions], device=device)
        unobserved = torch.as_tensor(issue_sample.unobserved_ids[positions], device=device)
        user_vectors, issue_vectors, repository_users, repository_vectors = (
            model.joint_embeddings()
        )
        issue_loss = criterion(
            (user_vectors[users] * issue_vectors[positive]).sum(-1),
            (user_vectors[users] * issue_vectors[unobserved]).sum(-1),
        )
        loss = issue_loss
        repository_entries = [
            (position, repository_by_query[int(row)])
            for position, row in enumerate(rows)
            if int(row) in repository_by_query
        ]
        if repository_entries:
            positions = torch.as_tensor(
                [entry[0] for entry in repository_entries], device=device
            )
            repo_positive = torch.as_tensor(
                [entry[1][0] for entry in repository_entries], device=device
            )
            repo_unobserved = torch.as_tensor(
                [entry[1][1] for entry in repository_entries], device=device
            )
            assert repository_users is not None and repository_vectors is not None
            repository_loss = criterion(
                (repository_users[users[positions]] * repository_vectors[repo_positive]).sum(-1),
                (repository_users[users[positions]] * repository_vectors[repo_unobserved]).sum(-1),
            )
            loss = issue_loss + repository_weight * repository_loss
            repository_total += float(repository_loss.detach()) * len(repository_entries)
            repository_count += len(repository_entries)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        issue_total += float(issue_loss.detach()) * len(indices)
        processed += len(indices)
        if progress_callback is not None:
            rss, available, cuda_allocated, cuda_peak = _resource_snapshot(device)
            progress = BatchProgress(
                batch_number,
                total_batches,
                processed,
                len(order),
                issue_total / processed,
                repository_total / repository_count if repository_count else None,
                time.perf_counter() - batch_started, rss, available, cuda_allocated, cuda_peak,
            )
            progress_callback(progress)
        else:
            rss, available, cuda_allocated, cuda_peak = _resource_snapshot(device)
            progress = BatchProgress(
                batch_number, total_batches, processed, len(order), issue_total / processed,
                repository_total / repository_count if repository_count else None,
                time.perf_counter() - batch_started, rss, available, cuda_allocated, cuda_peak,
            )
        state = BatchResumeState(
            next_batch=batch_number,
            issue_loss_sum=issue_total,
            repository_loss_sum=repository_total,
            processed=processed,
            repository_count=repository_count,
        )
        if checkpoint_callback is not None:
            checkpoint_callback(progress, state)
        if stop_requested is not None and stop_requested():
            raise KeyboardInterrupt("safe stop requested after optimizer step")
    return EpochResult(
        issue_total / len(order),
        repository_total / repository_count if repository_count else None,
        len(order),
        repository_count,
    )


class TorchIssueRanker:
    """Adapter for the stage-2 shared candidate evaluator."""

    def __init__(
        self,
        model: LightGCN | CoderIssueModel,
        known_developers: np.ndarray,
        *,
        device: torch.device,
    ) -> None:
        self.model = model
        self.known_developers = known_developers.astype(bool)
        self.device = device
        self.name = getattr(getattr(model, "config", None), "variant", "lightgcn")
        model.eval()
        with torch.inference_mode():
            if isinstance(model, CoderIssueModel):
                users, issues = model.fused_embeddings()
            else:
                users, issues = model.propagated_embeddings()
        self.user_embeddings = users.detach().cpu().numpy()
        self.issue_embeddings = issues.detach().cpu().numpy()

    def is_predictable(self, developer_id: int) -> bool:
        return 0 <= developer_id < len(self.known_developers) and bool(
            self.known_developers[developer_id]
        )

    def score(self, developer_id: int, candidate_issue_ids: np.ndarray) -> np.ndarray:
        if not self.is_predictable(developer_id):
            return np.zeros(len(candidate_issue_ids), dtype=np.float64)
        return self.issue_embeddings[candidate_issue_ids] @ self.user_embeddings[developer_id]


def select_by_validation(
    candidates: list[tuple[dict[str, object], object]],
    evaluator: Callable[[object], dict[str, object]],
    *,
    group: str = "predictable",
    metric: str = "ndcg@10",
) -> tuple[dict[str, object], object, list[dict[str, object]]]:
    """Select without accepting test metrics as input."""

    if not candidates:
        raise ValueError("at least one model candidate is required")
    records: list[dict[str, object]] = []
    best: tuple[float, dict[str, object], object] | None = None
    for parameters, model in candidates:
        validation = evaluator(model)
        score = float(validation[group][metric])
        records.append({"parameters": parameters, "validation": validation, "selection_score": score})
        if best is None or score > best[0]:
            best = (score, parameters, model)
    assert best is not None
    return best[1], best[2], records
