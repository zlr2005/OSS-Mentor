"""Popularity, TF-IDF text-similarity, and BPR matrix-factorization baselines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import joblib
import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .dataset import Interactions, IssueCatalog


class Ranker(Protocol):
    name: str

    def score(self, developer_id: int, candidate_issue_ids: np.ndarray) -> np.ndarray: ...
    def is_predictable(self, developer_id: int) -> bool: ...


class PopularityModel:
    name = "popularity"

    def __init__(self, issue_repositories: np.ndarray, repository_counts: np.ndarray) -> None:
        self.issue_repositories = issue_repositories.astype(np.int64)
        self.repository_counts = repository_counts.astype(np.float64)

    @classmethod
    def fit(cls, train: Interactions, catalog: IssueCatalog, repository_count: int) -> "PopularityModel":
        counts = np.bincount(train.repository_ids, minlength=repository_count)
        return cls(catalog.repository_ids, counts)

    def score(self, developer_id: int, candidate_issue_ids: np.ndarray) -> np.ndarray:
        del developer_id
        return np.log1p(self.repository_counts[self.issue_repositories[candidate_issue_ids]])

    def is_predictable(self, developer_id: int) -> bool:
        del developer_id
        return True

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path / "model.npz",
            issue_repositories=self.issue_repositories,
            repository_counts=self.repository_counts,
        )
        (path / "metadata.json").write_text(
            json.dumps({"model": self.name, "score": "log1p(train repository resolution count)"}, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "PopularityModel":
        arrays = np.load(path / "model.npz")
        return cls(arrays["issue_repositories"], arrays["repository_counts"])


class TextSimilarityModel:
    name = "text_similarity"

    def __init__(
        self,
        vectorizer: TfidfVectorizer,
        issue_vectors: sparse.csr_matrix,
        user_profiles: sparse.csr_matrix,
        known_developers: np.ndarray,
    ) -> None:
        self.vectorizer = vectorizer
        self.issue_vectors = issue_vectors.tocsr()
        self.user_profiles = user_profiles.tocsr()
        self.known_developers = known_developers.astype(bool)

    @classmethod
    def fit(
        cls,
        train: Interactions,
        catalog: IssueCatalog,
        *,
        developer_count: int,
        max_features: int,
        min_df: int,
        ngram_max: int,
    ) -> "TextSimilarityModel":
        train_issue_ids = np.unique(train.issue_ids)
        vectorizer = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            max_features=max_features,
            min_df=min_df,
            ngram_range=(1, ngram_max),
            dtype=np.float32,
        )
        vectorizer.fit([catalog.texts[int(issue_id)] for issue_id in train_issue_ids])
        issue_vectors = vectorizer.transform(catalog.texts).tocsr()
        rows = train.developer_ids
        cols = train.issue_ids
        values = np.ones(len(train), dtype=np.float32)
        history = sparse.csr_matrix(
            (values, (rows, cols)),
            shape=(developer_count, catalog.size),
            dtype=np.float32,
        )
        history.data[:] = 1.0
        profiles = normalize(history @ issue_vectors, norm="l2", axis=1).tocsr()
        known = np.asarray(history.getnnz(axis=1) > 0).ravel()
        return cls(vectorizer, issue_vectors, profiles, known)

    def score(self, developer_id: int, candidate_issue_ids: np.ndarray) -> np.ndarray:
        if not self.is_predictable(developer_id):
            return np.zeros(len(candidate_issue_ids), dtype=np.float64)
        return np.asarray(
            self.issue_vectors[candidate_issue_ids].dot(self.user_profiles[developer_id].T).toarray()
        ).ravel()

    def is_predictable(self, developer_id: int) -> bool:
        return 0 <= developer_id < len(self.known_developers) and bool(self.known_developers[developer_id])

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.vectorizer, path / "vectorizer.joblib", compress=3)
        sparse.save_npz(path / "issue_vectors.npz", self.issue_vectors, compressed=True)
        sparse.save_npz(path / "user_profiles.npz", self.user_profiles, compressed=True)
        np.save(path / "known_developers.npy", self.known_developers)
        (path / "metadata.json").write_text(
            json.dumps({"model": self.name, "profile": "mean normalized training issue TF-IDF"}, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "TextSimilarityModel":
        return cls(
            joblib.load(path / "vectorizer.joblib"),
            sparse.load_npz(path / "issue_vectors.npz"),
            sparse.load_npz(path / "user_profiles.npz"),
            np.load(path / "known_developers.npy"),
        )


class BPRModel:
    name = "bpr_mf"

    def __init__(self, user_factors: np.ndarray, item_factors: np.ndarray, known_developers: np.ndarray) -> None:
        self.user_factors = user_factors.astype(np.float32)
        self.item_factors = item_factors.astype(np.float32)
        self.known_developers = known_developers.astype(bool)

    @classmethod
    def fit(
        cls,
        train: Interactions,
        negative_pools: np.ndarray,
        *,
        developer_count: int,
        issue_count: int,
        dimensions: int,
        learning_rate: float,
        regularization: float,
        epochs: int,
        batch_size: int,
        seed: int,
    ) -> "BPRModel":
        if negative_pools.shape[0] != len(train) or negative_pools.shape[1] == 0:
            raise ValueError("negative pools must have one non-empty row per training interaction")
        rng = np.random.default_rng(seed)
        scale = 0.1 / np.sqrt(dimensions)
        users = rng.normal(0.0, scale, (developer_count, dimensions)).astype(np.float32)
        items = rng.normal(0.0, scale, (issue_count, dimensions)).astype(np.float32)
        order = np.arange(len(train))
        for epoch in range(epochs):
            rng.shuffle(order)
            selected_negatives = negative_pools[:, epoch % negative_pools.shape[1]]
            for start in range(0, len(order), batch_size):
                indices = order[start : start + batch_size]
                user_ids = train.developer_ids[indices]
                positive_ids = train.issue_ids[indices]
                negative_ids = selected_negatives[indices]
                user_old = users[user_ids].copy()
                positive_old = items[positive_ids].copy()
                negative_old = items[negative_ids].copy()
                margin = np.sum(user_old * (positive_old - negative_old), axis=1)
                gradient = 1.0 / (1.0 + np.exp(np.clip(margin, -30.0, 30.0)))
                np.add.at(
                    users,
                    user_ids,
                    learning_rate * (gradient[:, None] * (positive_old - negative_old) - regularization * user_old),
                )
                np.add.at(
                    items,
                    positive_ids,
                    learning_rate * (gradient[:, None] * user_old - regularization * positive_old),
                )
                np.add.at(
                    items,
                    negative_ids,
                    learning_rate * (-gradient[:, None] * user_old - regularization * negative_old),
                )
        known = np.bincount(train.developer_ids, minlength=developer_count) > 0
        return cls(users, items, known)

    def score(self, developer_id: int, candidate_issue_ids: np.ndarray) -> np.ndarray:
        if not self.is_predictable(developer_id):
            return np.zeros(len(candidate_issue_ids), dtype=np.float64)
        return self.item_factors[candidate_issue_ids] @ self.user_factors[developer_id]

    def is_predictable(self, developer_id: int) -> bool:
        return 0 <= developer_id < len(self.known_developers) and bool(self.known_developers[developer_id])

    def save(self, path: Path, *, metadata: dict[str, object] | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path / "model.npz",
            user_factors=self.user_factors,
            item_factors=self.item_factors,
            known_developers=self.known_developers,
        )
        payload = {"model": self.name, **(metadata or {})}
        (path / "metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "BPRModel":
        arrays = np.load(path / "model.npz")
        return cls(arrays["user_factors"], arrays["item_factors"], arrays["known_developers"])
