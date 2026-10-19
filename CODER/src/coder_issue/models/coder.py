"""CODER-inspired issue model with optional semantic, structure, and project paths."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import torch
from torch import Tensor, nn

from .gat import StructuralGATConfig, StructuralGATEncoder
from .lightgcn import LightGCN, LightGCNConfig


@dataclass(frozen=True)
class CoderConfig:
    num_users: int
    num_issues: int
    num_repositories: int
    embedding_dim: int = 32
    issue_layers: int = 4
    repository_layers: int = 2
    structure_layers: int = 2
    structure_heads: int = 2
    structure_dropout: float = 0.1
    semantic_dim: int = 0
    use_text: bool = False
    use_structure: bool = False
    use_repository: bool = False

    @property
    def variant(self) -> str:
        if self.use_repository:
            return "coder_issue_repo"
        if self.use_text and self.use_structure:
            return "coder_issue_base"
        if self.use_text:
            return "coder_issue_text"
        if self.use_structure:
            return "coder_issue_structure"
        return "lightgcn"


def _mlp(input_dim: int, output_dim: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(input_dim, output_dim),
        nn.ReLU(),
        nn.Linear(output_dim, output_dim),
    )


class CoderIssueModel(nn.Module):
    """Fuse independently switchable collaborative/content/structure features."""

    def __init__(
        self,
        config: CoderConfig,
        *,
        issue_adjacency: Tensor,
        issue_to_repository: Tensor,
        repository_adjacency: Tensor | None = None,
        semantic_embeddings: Tensor | None = None,
        structural_encoder: StructuralGATEncoder | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.issue_graph = LightGCN(
            LightGCNConfig(config.num_users, config.num_issues, config.embedding_dim, config.issue_layers),
            issue_adjacency,
        )
        self.register_buffer("issue_to_repository", issue_to_repository.to(torch.long))
        if self.issue_to_repository.shape != (config.num_issues,):
            raise ValueError("issue_to_repository must contain one row per Issue")

        self.repository_graph: LightGCN | None = None
        if config.use_repository:
            if repository_adjacency is None:
                raise ValueError("repository adjacency is required when use_repository=true")
            self.repository_graph = LightGCN(
                LightGCNConfig(
                    config.num_users, config.num_repositories, config.embedding_dim,
                    config.repository_layers,
                ),
                repository_adjacency,
            )

        if config.use_text:
            if semantic_embeddings is None or semantic_embeddings.shape != (
                config.num_issues, config.semantic_dim
            ):
                raise ValueError("row-aligned semantic embeddings are required")
            self.register_buffer("semantic_embeddings", semantic_embeddings.to(torch.float32))
        else:
            self.register_buffer("semantic_embeddings", torch.empty(0))

        if config.use_structure and structural_encoder is None:
            raise ValueError("structural encoder is required when use_structure=true")
        self.structural_encoder = structural_encoder

        enhanced = config.use_text or config.use_structure or config.use_repository
        user_width = config.embedding_dim * (2 if config.use_repository else 1)
        issue_width = config.embedding_dim
        issue_width += config.semantic_dim if config.use_text else 0
        issue_width += config.embedding_dim if config.use_structure else 0
        issue_width += config.embedding_dim if config.use_repository else 0
        self.user_fusion = _mlp(user_width, config.embedding_dim) if enhanced else nn.Identity()
        self.issue_fusion = _mlp(issue_width, config.embedding_dim) if enhanced else nn.Identity()

    def fused_embeddings(self) -> tuple[Tensor, Tensor]:
        issue_users, collaborative_issues = self.issue_graph.propagated_embeddings()
        user_parts = [issue_users]
        issue_parts = [collaborative_issues]
        if self.config.use_text:
            issue_parts.append(self.semantic_embeddings)
        if self.config.use_structure:
            _, structural_issues, _ = self.structural_encoder()
            issue_parts.append(structural_issues)
        if self.config.use_repository:
            repository_users, repositories = self.repository_graph.propagated_embeddings()
            user_parts.append(repository_users)
            issue_parts.append(repositories[self.issue_to_repository])
        return self.user_fusion(torch.cat(user_parts, dim=-1)), self.issue_fusion(
            torch.cat(issue_parts, dim=-1)
        )

    def issue_pair_scores(self, user_ids: Tensor, issue_ids: Tensor) -> Tensor:
        users, issues = self.fused_embeddings()
        return (users[user_ids] * issues[issue_ids]).sum(dim=-1)

    def repository_pair_scores(self, user_ids: Tensor, repository_ids: Tensor) -> Tensor:
        if self.repository_graph is None:
            raise RuntimeError("repository scoring is disabled for this variant")
        users, repositories = self.repository_graph.propagated_embeddings()
        return (users[user_ids] * repositories[repository_ids]).sum(dim=-1)

    @torch.no_grad()
    def score_known_users(self, user_ids: Tensor, candidate_issue_ids: Tensor) -> Tensor:
        users, issues = self.fused_embeddings()
        if candidate_issue_ids.ndim == 1:
            return users[user_ids] @ issues[candidate_issue_ids].T
        return torch.einsum("bd,bcd->bc", users[user_ids], issues[candidate_issue_ids])

    @torch.no_grad()
    def score_known_users_repositories(
        self, user_ids: Tensor, candidate_repository_ids: Tensor
    ) -> Tensor:
        if self.repository_graph is None:
            raise RuntimeError("repository scoring is disabled for this variant")
        users, repositories = self.repository_graph.propagated_embeddings()
        if candidate_repository_ids.ndim == 1:
            return users[user_ids] @ repositories[candidate_repository_ids].T
        return torch.einsum("bd,bcd->bc", users[user_ids], repositories[candidate_repository_ids])

    def save_bundle(self, path: Path, *, metadata: dict[str, object]) -> None:
        required = {
            "model_version", "dataset_version", "graph_version", "encoder_version",
            "profile_mode", "coverage_state",
        }
        missing = required - metadata.keys()
        if missing:
            raise ValueError(f"missing model provenance fields: {sorted(missing)}")
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), path / "weights.pt")
        payload = {
            "model": "coder_issue",
            "variant": self.config.variant,
            "config": asdict(self.config),
            **metadata,
        }
        (path / "model_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    @classmethod
    def load_bundle(cls, path: Path, *, map_location: str | torch.device = "cpu") -> "CoderIssueModel":
        manifest = json.loads((path / "model_manifest.json").read_text(encoding="utf-8"))
        config = CoderConfig(**manifest["config"])
        state = torch.load(path / "weights.pt", map_location=map_location, weights_only=True)
        structural = None
        if config.use_structure:
            structural_config = StructuralGATConfig(
                config.num_repositories,
                config.num_issues,
                int(state["structural_encoder.node_embeddings.weight"].shape[0])
                - config.num_repositories - config.num_issues,
                config.embedding_dim,
                config.structure_layers,
                config.structure_heads,
                config.structure_dropout,
            )
            structural = StructuralGATEncoder(structural_config, state["structural_encoder.edge_index"])
        model = cls(
            config,
            issue_adjacency=state["issue_graph.adjacency"],
            issue_to_repository=state["issue_to_repository"],
            repository_adjacency=state.get("repository_graph.adjacency"),
            semantic_embeddings=state.get("semantic_embeddings"),
            structural_encoder=structural,
        )
        model.load_state_dict(state)
        return model
