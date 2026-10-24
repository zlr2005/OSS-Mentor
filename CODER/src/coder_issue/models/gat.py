"""Dependency-light graph attention for Repository-Issue-Label structure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import torch
from torch import Tensor, nn
import torch.nn.functional as functional


def bidirectional_structural_edges(
    issue_repository_edges: Tensor,
    issue_label_edges: Tensor,
    *,
    num_repositories: int,
    num_issues: int,
    num_labels: int,
) -> Tensor:
    """Offset node IDs and return bidirectional Repo-Issue-Label edges."""

    del num_labels
    issue_offset = num_repositories
    label_offset = num_repositories + num_issues
    repo_issue = torch.stack(
        [issue_repository_edges[:, 1], issue_repository_edges[:, 0] + issue_offset]
    )
    issue_label = torch.stack(
        [issue_label_edges[:, 0] + issue_offset, issue_label_edges[:, 1] + label_offset]
    )
    forward = torch.cat([repo_issue, issue_label], dim=1).to(torch.long)
    return torch.unique(torch.cat([forward, forward.flip(0)], dim=1), dim=1)


class GATLayer(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, heads: int = 2, dropout: float = 0.0) -> None:
        super().__init__()
        if output_dim % heads:
            raise ValueError("output_dim must be divisible by heads")
        self.heads = heads
        self.head_dim = output_dim // heads
        self.dropout = dropout
        self.linear = nn.Linear(input_dim, output_dim, bias=False)
        self.attention_source = nn.Parameter(torch.empty(heads, self.head_dim))
        self.attention_target = nn.Parameter(torch.empty(heads, self.head_dim))
        self.residual = nn.Identity() if input_dim == output_dim else nn.Linear(input_dim, output_dim, bias=False)
        nn.init.xavier_uniform_(self.linear.weight)
        nn.init.xavier_uniform_(self.attention_source)
        nn.init.xavier_uniform_(self.attention_target)

    def forward(self, features: Tensor, edge_index: Tensor) -> Tensor:
        node_count = features.shape[0]
        source, target = edge_index
        projected = self.linear(features).view(node_count, self.heads, self.head_dim)
        logits = (
            (projected[source] * self.attention_source).sum(-1)
            + (projected[target] * self.attention_target).sum(-1)
        )
        logits = functional.leaky_relu(logits, negative_slope=0.2)
        target_index = target[:, None].expand(-1, self.heads)
        maximum = torch.full(
            (node_count, self.heads), -torch.inf, dtype=logits.dtype, device=logits.device
        )
        maximum.scatter_reduce_(0, target_index, logits, reduce="amax", include_self=True)
        exponent = torch.exp(logits - maximum[target])
        denominator = torch.zeros_like(maximum)
        denominator.index_add_(0, target, exponent)
        attention = exponent / denominator[target].clamp_min(1e-12)
        attention = functional.dropout(attention, self.dropout, self.training)
        output = torch.zeros_like(projected)
        output.index_add_(0, target, attention[..., None] * projected[source])
        return output.flatten(1) + self.residual(features)


@dataclass(frozen=True)
class StructuralGATConfig:
    num_repositories: int
    num_issues: int
    num_labels: int
    embedding_dim: int = 32
    layers: int = 2
    heads: int = 2
    dropout: float = 0.1


class StructuralGATEncoder(nn.Module):
    def __init__(self, config: StructuralGATConfig, edge_index: Tensor) -> None:
        super().__init__()
        self.config = config
        node_count = config.num_repositories + config.num_issues + config.num_labels
        self.node_embeddings = nn.Embedding(node_count, config.embedding_dim)
        nn.init.xavier_uniform_(self.node_embeddings.weight)
        self.layers = nn.ModuleList(
            GATLayer(config.embedding_dim, config.embedding_dim, config.heads, config.dropout)
            for _ in range(config.layers)
        )
        self.register_buffer("edge_index", edge_index.to(torch.long))

    def forward(self) -> tuple[Tensor, Tensor, Tensor]:
        features = self.node_embeddings.weight
        for layer in self.layers:
            features = functional.elu(layer(features, self.edge_index))
        repo_end = self.config.num_repositories
        issue_end = repo_end + self.config.num_issues
        return features[:repo_end], features[repo_end:issue_end], features[issue_end:]

    def save_bundle(self, path: Path, *, metadata: dict[str, object] | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), path / "weights.pt")
        payload = {"model": "structural_gat", "config": asdict(self.config), **(metadata or {})}
        (path / "model_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    @classmethod
    def load_bundle(cls, path: Path, *, map_location: str | torch.device = "cpu") -> "StructuralGATEncoder":
        manifest = json.loads((path / "model_manifest.json").read_text(encoding="utf-8"))
        config = StructuralGATConfig(**manifest["config"])
        state = torch.load(path / "weights.pt", map_location=map_location, weights_only=True)
        model = cls(config, state["edge_index"])
        model.load_state_dict(state)
        return model
