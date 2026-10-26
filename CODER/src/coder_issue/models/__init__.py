"""Trainable CODER-inspired model components."""

from .coder import CoderConfig, CoderIssueModel
from .gat import StructuralGATConfig, StructuralGATEncoder
from .lightgcn import LightGCN, LightGCNConfig
from .profile import ProfileAdapterConfig, ProfileResidualAdapter, tensorize_confirmed_profile

__all__ = [
    "CoderConfig",
    "CoderIssueModel",
    "LightGCN",
    "LightGCNConfig",
    "StructuralGATConfig",
    "StructuralGATEncoder",
    "ProfileAdapterConfig",
    "ProfileResidualAdapter",
    "tensorize_confirmed_profile",
]
