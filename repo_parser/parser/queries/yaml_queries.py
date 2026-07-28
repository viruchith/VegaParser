"""Tree-sitter extraction for YAML files with Kubernetes manifest support."""

from __future__ import annotations

import re

from repo_parser.models import ClassInfo, ParsedFile
from repo_parser.parser.queries.base import child_count, iter_nodes, line_number, node_kind, node_text, parse_root

K8S_KINDS = {
    "Deployment", "Service", "Ingress", "ConfigMap", "Secret", "Pod",
    "StatefulSet", "DaemonSet", "Job", "CronJob", "Namespace",
    "PersistentVolume", "PersistentVolumeClaim", "ServiceAccount",
    "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding",
    "HorizontalPodAutoscaler", "NetworkPolicy", "ReplicaSet",
}

K8S_KEY_RE = re.compile(
    r"^(apiVersion|kind|metadata|name|namespace|labels|annotations|spec|replicas|containers|image|port|selector)$"
)


def _extract_yaml_pairs(source: str, root) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in iter_nodes(root, "block_mapping_pair"):
        children = [node.child(i) for i in range(child_count(node))]
        if len(children) < 2:
            continue
        key = node_text(source, children[0]).strip().strip('"').strip("'")
        val = node_text(source, children[1]).strip()
        if key and val and len(val) < 200:
            pairs[key] = val
    return pairs


def _is_k8s_manifest(pairs: dict[str, str]) -> bool:
    return "apiVersion" in pairs and "kind" in pairs


def parse_yaml(filepath: str, source: str, parser) -> ParsedFile:
    _tree, root = parse_root(parser, source)

    parsed = ParsedFile(filepath=filepath, language="yaml")
    pairs = _extract_yaml_pairs(source, root)

    # Collect top-level keys as exports
    for key in pairs:
        if K8S_KEY_RE.match(key):
            parsed.exports.append(f"{key}: {pairs[key]}")

    if _is_k8s_manifest(pairs):
        parsed.language = "kubernetes"
        kind = pairs.get("kind", "Resource")
        name = pairs.get("name", pairs.get("metadata", "unnamed"))
        api_version = pairs.get("apiVersion", "")

        parsed.module_docstring = (
            f"Kubernetes {kind} manifest"
            + (f" (apiVersion: {api_version})" if api_version else "")
            + (f"\nResource name: {name}" if name else "")
        )
        parsed.imports.append(f"apiVersion: {api_version}")
        parsed.imports.append(f"kind: {kind}")

        parsed.classes.append(
            ClassInfo(
                name=f"{kind}/{name}",
                docstring=f"Kubernetes {kind} resource",
                line_start=1,
                line_end=source.count("\n") + 1,
            )
        )

        # Extract container images from raw source for K8s workloads
        for match in re.finditer(r"^\s*image:\s*(.+)$", source, re.MULTILINE):
            image = match.group(1).strip().strip('"').strip("'")
            if image:
                parsed.imports.append(f"image: {image}")

        if kind in K8S_KINDS:
            parsed.exports.insert(0, f"K8s/{kind}")

    else:
        # Generic YAML — list top-level keys
        top_keys = list(pairs.keys())[:30]
        if top_keys:
            parsed.module_docstring = "YAML configuration keys: " + ", ".join(top_keys)

    return parsed
