"""Read-only readiness and resource checks for full-model training."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from typing import Any

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import torch

from coder_issue.baselines.dataset import mapping_sizes
from coder_issue.models.text_encoder import EmbeddingCache
from .config import NeuralSettings, expand_search_trials, validate_neural_inputs


def _memory() -> dict[str, int | None]:
    try:
        import psutil
        memory = psutil.virtual_memory()
        process = psutil.Process()
        return {"physical_total": memory.total, "physical_available": memory.available,
                "process_rss": process.memory_info().rss}
    except ImportError:
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                status = MEMORYSTATUSEX(); status.dwLength = ctypes.sizeof(status)
                if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                    raise OSError("GlobalMemoryStatusEx failed")
                counters = PROCESS_MEMORY_COUNTERS(); counters.cb = ctypes.sizeof(counters)
                get_current = ctypes.windll.kernel32.GetCurrentProcess
                get_current.restype = wintypes.HANDLE
                handle = get_current()
                get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
                get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
                get_memory.restype = wintypes.BOOL
                if not get_memory(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    raise OSError("GetProcessMemoryInfo failed")
                return {"physical_total": int(status.ullTotalPhys),
                        "physical_available": int(status.ullAvailPhys),
                        "process_rss": int(counters.WorkingSetSize)}
            except Exception:
                pass
        return {"physical_total": None, "physical_available": None, "process_rss": None}


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".preflight-", dir=path)
        os.close(handle)
        Path(name).unlink()
        return True
    except OSError:
        return False


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _encoder_files(option, *, allow_download: bool, device: str) -> dict[str, Any]:
    result = {"name": option.name, "revision": option.revision, "allow_download": allow_download}
    try:
        from transformers import AutoConfig, AutoModel, AutoTokenizer
        from transformers.utils.hub import cached_file
        required = {}
        for filename in ("config.json", "tokenizer_config.json"):
            required[filename] = cached_file(
                option.name, filename, revision=option.revision,
                local_files_only=True, _raise_exceptions_for_missing_entries=False,
                _raise_exceptions_for_connection_errors=False,
            )
        weights = None
        for filename in ("model.safetensors", "pytorch_model.bin"):
            weights = cached_file(
                option.name, filename, revision=option.revision,
                local_files_only=True, _raise_exceptions_for_missing_entries=False,
                _raise_exceptions_for_connection_errors=False,
            )
            if weights:
                break
        required["weights"] = weights
        config_ok = tokenizer_ok = False
        try:
            AutoConfig.from_pretrained(
                option.name, revision=option.revision, local_files_only=True
            )
            config_ok = True
            AutoTokenizer.from_pretrained(
                option.name, revision=option.revision, local_files_only=True
            )
            tokenizer_ok = True
        except Exception as exc:
            result["load_check_error"] = f"{type(exc).__name__}: {exc}"
        model_loadable = False
        if weights and config_ok and tokenizer_ok:
            try:
                model = AutoModel.from_pretrained(
                    option.name, revision=option.revision, local_files_only=True
                ).to(device)
                model.eval(); del model
                model_loadable = True
            except Exception as exc:
                result["model_load_error"] = f"{type(exc).__name__}: {exc}"
        result.update(
            status="READY" if model_loadable else "MISSING",
            files=required, config_loadable=config_ok, tokenizer_loadable=tokenizer_ok,
            model_loadable_on_configured_device=model_loadable,
        )
    except Exception as exc:
        result.update(status="UNAVAILABLE", error=f"{type(exc).__name__}: {exc}")
    return result


def _trial_grid(settings: NeuralSettings, variant: str) -> list[dict[str, Any]]:
    use_text = variant in {"coder_issue_text", "coder_issue_base", "coder_issue_repo"}
    use_structure = variant in {"coder_issue_structure", "coder_issue_base", "coder_issue_repo"}
    use_repository = variant == "coder_issue_repo"
    encoders = tuple(f"{item.name}@{item.revision}" for item in settings.encoders)
    return list(
        expand_search_trials(
            settings,
            use_text=use_text,
            use_structure=use_structure,
            use_repository=use_repository,
            encoder_versions=encoders,
        )
    )


def temporal_visibility_audit(settings: NeuralSettings) -> dict[str, Any]:
    split_manifest = json.loads(
        (settings.baseline.graph_dir / "split_manifest.json").read_text(encoding="utf-8")
    )
    train_rule = str(split_manifest["train"])
    cutoff_text = train_rule.split("<", 1)[1].strip()
    issues = pq.read_table(
        settings.baseline.normalized_dir / "issues.parquet", columns=["issue_id", "created_at"]
    )
    cutoff = np.datetime64(cutoff_text.replace("+00:00", ""))
    created = issues["created_at"].to_numpy(zero_copy_only=False).astype("datetime64[us]")
    future_ids = set(issues["issue_id"].to_numpy()[created >= cutoff].tolist())
    repo_edges = pq.read_table(
        settings.baseline.normalized_dir / "issue_repo_edges.parquet", columns=["issue_id"]
    )["issue_id"].to_numpy()
    label_table = pq.read_table(
        settings.baseline.normalized_dir / "issue_label_edges.parquet",
        columns=["issue_id", "snapshot_type"],
    )
    label_table = label_table.filter(pc.equal(label_table["snapshot_type"], "creation_snapshot"))
    label_ids = label_table["issue_id"].to_numpy()
    future_repo = sum(int(value) in future_ids for value in repo_edges)
    future_label = sum(int(value) in future_ids for value in label_ids)
    return {
        "train_cutoff": cutoff_text,
        "issues_created_at_or_after_cutoff": len(future_ids),
        "future_issue_repository_edges_in_structural_graph": future_repo,
        "future_issue_creation_label_edges_in_structural_graph": future_label,
        "participates_in_training_gat_message_passing": bool(future_repo or future_label),
        "result_field_or_closure_label_used": False,
        "conclusion": (
            "TIME_VISIBILITY_RISK: creation-time fields avoid outcome leakage, but the current full "
            "structural graph includes nodes created after the training cutoff and permits their "
            "messages during training. Keep current graph unchanged for protocol compatibility; "
            "a new graph_version should filter nodes/edges by prediction cutoff before future formal comparisons."
            if future_repo or future_label else "NO_POST_CUTOFF_STRUCTURAL_NODES"
        ),
    }


def run_preflight(settings: NeuralSettings, *, variant: str = "coder_issue_repo") -> dict[str, Any]:
    hashes = validate_neural_inputs(settings, require_text=False)
    developers, issues, repositories = mapping_sizes(settings.baseline)
    labels = pq.ParquetFile(
        settings.baseline.normalized_dir / "mappings/label_mapping.parquet"
    ).metadata.num_rows
    train_rows = pq.ParquetFile(settings.baseline.graph_dir / "train.parquet").metadata.num_rows
    repo_edges = pq.ParquetFile(
        settings.baseline.normalized_dir / "issue_repo_edges.parquet"
    ).metadata.num_rows
    label_edges = pq.ParquetFile(
        settings.baseline.normalized_dir / "issue_label_edges.parquet"
    ).metadata.num_rows
    mapping_checks: dict[str, bool] = {}
    for name, expected in (("developer", developers), ("issue", issues),
                           ("repository", repositories), ("label", labels)):
        column = f"{name}_id"
        values = pq.read_table(
            settings.baseline.normalized_dir / f"mappings/{name}_mapping.parquet",
            columns=[column],
        )[column].to_numpy(zero_copy_only=False)
        mapping_checks[name] = bool(
            len(values) == expected and np.array_equal(values, np.arange(expected))
        )
    if not all(mapping_checks.values()):
        raise ValueError(f"mapping IDs are not contiguous/aligned: {mapping_checks}")
    issue_repo_table = pq.read_table(
        settings.baseline.normalized_dir / "issue_repo_edges.parquet",
        columns=["issue_id", "repository_id"],
    )
    issue_repo_issue = issue_repo_table["issue_id"].to_numpy(zero_copy_only=False)
    issue_repo_repository = issue_repo_table["repository_id"].to_numpy(zero_copy_only=False)
    label_table = pq.read_table(
        settings.baseline.normalized_dir / "issue_label_edges.parquet",
        columns=["issue_id", "label_id"],
    )
    label_issue = label_table["issue_id"].to_numpy(zero_copy_only=False)
    label_ids = label_table["label_id"].to_numpy(zero_copy_only=False)
    train_table = pq.read_table(
        settings.baseline.graph_dir / "train.parquet",
        columns=["developer_id", "issue_id", "repository_id"],
    )
    edge_checks = {
        "one_repository_per_issue": bool(
            len(issue_repo_issue) == issues and len(np.unique(issue_repo_issue)) == issues
        ),
        "issue_repository_bounds": bool(
            issue_repo_issue.min(initial=0) >= 0 and issue_repo_issue.max(initial=-1) < issues
            and issue_repo_repository.min(initial=0) >= 0
            and issue_repo_repository.max(initial=-1) < repositories
        ),
        "issue_label_bounds": bool(
            label_issue.min(initial=0) >= 0 and label_issue.max(initial=-1) < issues
            and label_ids.min(initial=0) >= 0 and label_ids.max(initial=-1) < labels
        ),
        "train_bounds": bool(
            train_table["developer_id"].to_numpy().min(initial=0) >= 0
            and train_table["developer_id"].to_numpy().max(initial=-1) < developers
            and train_table["issue_id"].to_numpy().min(initial=0) >= 0
            and train_table["issue_id"].to_numpy().max(initial=-1) < issues
            and train_table["repository_id"].to_numpy().min(initial=0) >= 0
            and train_table["repository_id"].to_numpy().max(initial=-1) < repositories
        ),
    }
    if not all(edge_checks.values()):
        raise ValueError(f"graph/mapping consistency failed: {edge_checks}")
    trials = _trial_grid(settings, variant)
    dimensions = [int(row["embedding_dim"]) for row in trials]
    max_dim = max(dimensions)
    # Documented engineering estimate: fp32 parameters + Adam moments + per-layer activations.
    trainable_values = (
        (developers + issues + developers + repositories + repositories + issues + labels) * max_dim
        + (2 * max_dim * max_dim) * 2
    )
    parameter_bytes = trainable_values * 4
    optimizer_bytes = parameter_bytes * 2
    graph_bytes = (train_rows * 4 + repo_edges * 2 + label_edges * 2) * 20
    semantic_dimensions: list[int] = []
    if variant in {"coder_issue_text", "coder_issue_base", "coder_issue_repo"}:
        for option in settings.encoders:
            manifest_path = (
                settings.embedding_dir
                / f"{option.name.replace('/', '__')}@{option.revision}"
                / "embedding_manifest.json"
            )
            if manifest_path.is_file():
                try:
                    semantic_dimensions.append(
                        int(json.loads(manifest_path.read_text(encoding="utf-8"))["dimension"])
                    )
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    pass
    semantic_buffer_bytes = issues * max(semantic_dimensions, default=0) * 4
    activation_bytes = (developers + issues + repositories + labels) * max_dim * 4 * (
        max(settings.issue_layers) + max(settings.structure_layers) + settings.repository_layers + 4
    )
    best_checkpoint_bytes = parameter_bytes + graph_bytes + semantic_buffer_bytes
    recovery_checkpoint_bytes = best_checkpoint_bytes + optimizer_bytes
    checkpoint_disk_bytes = (
        len(trials) * best_checkpoint_bytes + 2 * recovery_checkpoint_bytes
    )
    estimate = {
        "basis": "fp32 tensors; sparse edge indices/values approximated at 20 bytes per directed edge",
        "parameter_bytes": parameter_bytes,
        "adam_moment_bytes": optimizer_bytes,
        "static_graph_bytes": graph_bytes,
        "activation_upper_bound_bytes": activation_bytes,
        "estimated_training_working_set_bytes": parameter_bytes + optimizer_bytes + graph_bytes + activation_bytes,
        "semantic_buffer_bytes": semantic_buffer_bytes,
        "estimated_best_checkpoint_bytes_per_trial": best_checkpoint_bytes,
        "estimated_recovery_checkpoint_bytes": recovery_checkpoint_bytes,
        "estimated_checkpoint_disk_bytes": checkpoint_disk_bytes,
        "checkpoint_retention_basis": (
            "one best checkpoint per Trial plus rotating latest/previous recovery checkpoints"
        ),
        "warning": "This is a sizing estimate, not an exact peak or runtime prediction; validate with --probe-batch.",
    }
    memory_info = _memory()
    available = memory_info.get("physical_available")
    estimated_working = int(estimate["estimated_training_working_set_bytes"])
    if available is None:
        cpu_risk = "UNKNOWN"
    elif available < 2 * 1024**3 or estimated_working > available * 0.5:
        cpu_risk = "HIGH"
    elif estimated_working > available * 0.25:
        cpu_risk = "MODERATE"
    else:
        cpu_risk = "LOW_BY_MODEL_TENSOR_ESTIMATE"
    estimate["cpu_memory_risk"] = cpu_risk
    encoder_checks = [
        _encoder_files(x, allow_download=settings.allow_download, device=settings.device)
        for x in settings.encoders
    ]
    cache_checks = []
    for option, encoder_check in zip(settings.encoders, encoder_checks, strict=True):
        safe = option.name.replace("/", "__")
        root = settings.embedding_dir / f"{safe}@{option.revision}"
        dimension = None
        config_path = (encoder_check.get("files") or {}).get("config.json")
        if config_path:
            try:
                dimension = int(json.loads(Path(config_path).read_text(encoding="utf-8"))["hidden_size"])
            except Exception:
                pass
        status, reasons = EmbeddingCache(root).inspect(
            dataset_version=settings.dataset_version,
            mapping_sha256=settings.baseline.locks["issue_mapping_sha256"],
            model_name=option.name, revision=option.revision, dimension=dimension,
            max_segments=settings.max_segments, row_count=issues,
        )
        cache_checks.append({"encoder": option.name, "root": str(root), "status": status,
                             "reasons": reasons})
    disk = shutil.disk_usage(settings.run_dir.parent)
    estimate["disk_risk"] = (
        "HIGH" if int(estimate["estimated_checkpoint_disk_bytes"]) > disk.free * 0.5
        else "LOW_BY_CHECKPOINT_ESTIMATE"
    )
    device = {"configured": settings.device, "cuda_available": torch.cuda.is_available()}
    if settings.device.startswith("cuda"):
        device["usable"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            device["name"] = torch.cuda.get_device_name(torch.device(settings.device))
            free_cuda, total_cuda = torch.cuda.mem_get_info(torch.device(settings.device))
            device["memory_free"] = free_cuda
            device["memory_total"] = total_cuda
            estimate["gpu_memory_risk"] = (
                "HIGH" if estimated_working > free_cuda * 0.7
                else "LOW_BY_MODEL_TENSOR_ESTIMATE"
            )
        else:
            device["reason"] = "PyTorch reports CUDA unavailable; configuration was not changed"
            estimate["gpu_memory_risk"] = "UNAVAILABLE"
    else:
        device["usable"] = settings.device == "cpu"
        estimate["gpu_memory_risk"] = "NOT_APPLICABLE_CPU_CONFIG"
    writable = {str(path): _writable(path) for path in (
        settings.run_dir, settings.model_dir, settings.embedding_dir)}
    text_required = variant in {"coder_issue_text", "coder_issue_base", "coder_issue_repo"}
    text_ready = any(row["status"] == "CACHE_READY" for row in cache_checks) or any(
        row["status"] == "READY" for row in encoder_checks
    )
    ready = bool(
        device["usable"] and all(writable.values()) and (not text_required or text_ready)
        and estimate["cpu_memory_risk"] != "HIGH"
        and estimate["disk_risk"] != "HIGH"
        and estimate["gpu_memory_risk"] != "HIGH"
    )
    return {
        "schema_version": 1,
        "status": "READY" if ready else "NOT_READY",
        "versions": {"python": sys.version.split()[0], "torch": torch.__version__,
                     "transformers": _package_version("transformers"),
                     "numpy": np.__version__, "pyarrow": _package_version("pyarrow"),
                     "platform": platform.platform()},
        "device": device,
        "memory": memory_info,
        "disk": {"path": str(settings.run_dir.parent), "free": disk.free, "total": disk.total},
        "writable": writable,
        "input_hashes": hashes,
        "entity_counts": {"developers": developers, "issues": issues,
                          "repositories": repositories, "labels": labels,
                          "train_interactions": train_rows},
        "mapping_consistency": mapping_checks,
        "edge_consistency": edge_checks,
        "search": {"variant": variant, "trial_count": len(trials),
                   "epochs_per_trial": settings.epochs,
                   "total_epochs": len(trials) * settings.epochs, "trials": trials},
        "resource_estimate": estimate,
        "transformer_weight_cache": encoder_checks,
        "issue_embedding_cache": cache_checks,
        "temporal_visibility_audit": temporal_visibility_audit(settings),
    }
