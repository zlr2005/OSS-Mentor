"""Developer-entity LightGCN with normalized propagation and bundle I/O."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn


@dataclass(frozen=True)
class LightGCNConfig:
    num_users: int
    num_items: int
    embedding_dim: int = 32
    layers: int = 4


def normalized_bipartite_adjacency(
    user_ids: Tensor,
    item_ids: Tensor,
    *,
    num_users: int,
    num_items: int,
) -> Tensor:
    """Build D^-1/2 A D^-1/2 for a bidirectional user-item graph."""

    if user_ids.ndim != 1 or item_ids.ndim != 1 or user_ids.shape != item_ids.shape:
        raise ValueError("user_ids and item_ids must be equal-length vectors")
    if user_ids.numel() == 0:
        raise ValueError("LightGCN requires at least one positive edge")
    item_nodes = item_ids.to(torch.long) + num_users
    users = user_ids.to(torch.long)
    sources = torch.cat([users, item_nodes])
    targets = torch.cat([item_nodes, users])
    node_count = num_users + num_items
    degree = torch.bincount(sources, minlength=node_count).to(torch.float32)
    weights = degree[sources].clamp_min(1).rsqrt() * degree[targets].clamp_min(1).rsqrt()
    return torch.sparse_coo_tensor(
        torch.stack([sources, targets]), weights, (node_count, node_count)
    ).coalesce()


class LightGCN(nn.Module):
    def __init__(self, config: LightGCNConfig, adjacency: Tensor) -> None:
        super().__init__()
        expected = config.num_users + config.num_items
        if adjacency.shape != (expected, expected) or not adjacency.is_sparse:
            raise ValueError("adjacency shape/type does not match LightGCN configuration")
        self.config = config
        self.embedding = nn.Embedding(expected, config.embedding_dim)
        nn.init.normal_(self.embedding.weight, std=0.1 / np.sqrt(config.embedding_dim))
        self.register_buffer("adjacency", adjacency.coalesce())

    def propagated_embeddings(self) -> tuple[Tensor, Tensor]:
        current = self.embedding.weight
        layers = [current]
        for _ in range(self.config.layers):
            current = torch.sparse.mm(self.adjacency, current)
            layers.append(current)
        averaged = torch.stack(layers, dim=0).mean(dim=0)
        return averaged[: self.config.num_users], averaged[self.config.num_users :]

    def pair_scores(self, user_ids: Tensor, item_ids: Tensor) -> Tensor:
        users, items = self.propagated_embeddings()
        return (users[user_ids] * items[item_ids]).sum(dim=-1)

    @torch.no_grad()
    def score_known_users(self, user_ids: Tensor, candidate_item_ids: Tensor) -> Tensor:
        """Score [batch, candidates] or one shared candidate vector."""

        users, items = self.propagated_embeddings()
        user_vectors = users[user_ids]
        if candidate_item_ids.ndim == 1:
            return user_vectors @ items[candidate_item_ids].T
        if candidate_item_ids.ndim != 2 or candidate_item_ids.shape[0] != user_ids.shape[0]:
            raise ValueError("candidate IDs must be [items] or [batch, items]")
        return torch.einsum("bd,bcd->bc", user_vectors, items[candidate_item_ids])

    @torch.no_grad()
    def export_embeddings(self, path: Path) -> None:
        users, items = self.propagated_embeddings()
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            user_embeddings=users.cpu().numpy(),
            item_embeddings=items.cpu().numpy(),
        )

    def save_bundle(self, path: Path, *, metadata: dict[str, object] | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), path / "weights.pt")
        payload = {"model": "lightgcn", "config": asdict(self.config), **(metadata or {})}
        (path / "model_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    @classmethod
    def load_bundle(cls, path: Path, *, map_location: str | torch.device = "cpu") -> "LightGCN":
        manifest = json.loads((path / "model_manifest.json").read_text(encoding="utf-8"))
        config = LightGCNConfig(**manifest["config"])
        state = torch.load(path / "weights.pt", map_location=map_location, weights_only=True)
        model = cls(config, state["adjacency"])
        model.load_state_dict(state)
        return model
