"""Coverage and cross-repository groups for stage-5 reporting."""

from __future__ import annotations

import numpy as np


def cross_repository_users(
    developer_ids: np.ndarray, repository_ids: np.ndarray
) -> set[int]:
    history: dict[int, set[int]] = {}
    for developer, repository in zip(developer_ids, repository_ids, strict=True):
        history.setdefault(int(developer), set()).add(int(repository))
    return {developer for developer, repositories in history.items() if len(repositories) > 1}


def project_module_coverage(
    evaluation_developers: np.ndarray,
    training_developers: np.ndarray,
    training_repositories: np.ndarray,
    evaluation_repositories: np.ndarray,
) -> dict[str, float | int]:
    known_users = set(int(value) for value in training_developers)
    known_repositories = set(int(value) for value in training_repositories)
    covered = np.asarray(
        [
            int(user) in known_users and int(repository) in known_repositories
            for user, repository in zip(
                evaluation_developers, evaluation_repositories, strict=True
            )
        ],
        dtype=bool,
    )
    users = set(int(value) for value in evaluation_developers)
    covered_users = {
        int(user)
        for user, repository in zip(
            evaluation_developers, evaluation_repositories, strict=True
        )
        if int(user) in known_users and int(repository) in known_repositories
    }
    return {
        "scene_count": int(len(covered)),
        "covered_scene_count": int(covered.sum()),
        "coverage": float(covered.mean()) if len(covered) else 0.0,
        "developer_count": len(users),
        "covered_developer_count": len(covered_users),
        "developer_coverage": len(covered_users) / len(users) if users else 0.0,
    }
