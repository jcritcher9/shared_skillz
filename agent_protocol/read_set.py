#!/usr/bin/env python3
"""Resolve a role's transitive protocol read-set.

Report only. Exit 1 when a reached file is neither declared for that role
nor marked situational. The declared map is hardcoded; README section 11
is a human copy written by 2C and is not parsed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


# Hardcoded machine source. README section 11 copies this map for humans
# and is not parsed. A slice that adds a file updates this map in the
# same commit.
ROLES: dict[str, dict[str, object]] = {
    "worker": {
        "entry": "agent_protocol/worker.md",
        "declared": frozenset(
            {
                "agent_protocol/worker.md",
                "agent_protocol/worktree.md",
                "agent_protocol/kernel.md",
            }
        ),
    },
    "critic": {
        "entry": "agent_protocol/critic.md",
        "declared": frozenset(
            {
                "agent_protocol/critic.md",
                "agent_protocol/kernel.md",
            }
        ),
    },
    "worktree": {
        "entry": "agent_protocol/worktree.md",
        "declared": frozenset(
            {
                "agent_protocol/worktree.md",
            }
        ),
    },
    "accept-and-land": {
        "entry": "agent_protocol/accept-and-land.md",
        "declared": frozenset(
            {
                "agent_protocol/accept-and-land.md",
            }
        ),
    },
    "closeout": {
        "entry": "agent_protocol/closeout.md",
        "declared": frozenset(
            {
                "agent_protocol/closeout.md",
                "agent_protocol/accept-and-land.md",
            }
        ),
    },
    "worker-area": {
        "entry": "agent_protocol/worker-area.md",
        "declared": frozenset(
            {
                "agent_protocol/worker-area.md",
                "agent_protocol/worktree.md",
                "agent_protocol/kernel.md",
            }
        ),
    },
}

SCHEMA_FILES = frozenset(
    {
        "CONTEXT_PILLARS.md",
        "implementation_notes_strategy.md",
    }
)

# Recognized as protocol files before they exist on disk. Declared maps
# still change in the slice that adds the file.
FUTURE_PROTOCOL = frozenset(
    {
        "agent_protocol/kernel.md",
        "agent_protocol/worker-area.md",
    }
)

SITUATIONAL_NAMES = frozenset(
    {
        "current_state.md",
        "testing.md",
        "open_work.md",
        "strategy_index.md",
        "handoff_doc.md",
    }
)

_FENCE_OPEN = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)")
_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})[ \t]*(?:\r?\n)?$")
_PROTOCOL_PATH = re.compile(r"agent_protocol/[A-Za-z0-9_./-]+\.(?:md|py)")
_TOKEN_BOUNDARY = r"[^A-Za-z0-9_./-]"


def repo_root_from_here() -> Path:
    here = Path(__file__).resolve().parent
    if (here / "worker.md").is_file() and (here.parent / "agent_protocol").is_dir():
        return here.parent
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "agent_protocol" / "worker.md").is_file():
            return candidate
    raise SystemExit("Could not find repository root (agent_protocol/worker.md missing).")


def strip_fences(text: str) -> str:
    """Drop valid Markdown fenced samples. Paths inside fences are not citations."""

    out: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        if fence_char is not None:
            closing = _FENCE_CLOSE.match(line)
            if closing is not None:
                marker = closing.group("marker")
                if marker[0] == fence_char and len(marker) >= fence_length:
                    fence_char = None
                    fence_length = 0
            continue

        opening = _FENCE_OPEN.match(line)
        if opening is not None:
            marker = opening.group("marker")
            info = opening.group("info")
            if marker[0] != "`" or "`" not in info:
                fence_char = marker[0]
                fence_length = len(marker)
                continue
        out.append(line)
    return "".join(out)


def protocol_relpaths(root: Path) -> dict[str, str]:
    """Map basename to repo-relative path for protocol and schema files."""

    found: dict[str, str] = {}
    for rel in SCHEMA_FILES | FUTURE_PROTOCOL:
        found[Path(rel).name] = rel
    proto = root / "agent_protocol"
    if proto.is_dir():
        for path in proto.rglob("*"):
            if path.suffix in {".md", ".py"} and path.is_file():
                rel = path.relative_to(root).as_posix()
                found[path.name] = rel
    for spec in ROLES.values():
        entry = str(spec["entry"])
        found[Path(entry).name] = entry
        for rel in spec["declared"]:  # type: ignore[union-attr]
            found[Path(str(rel)).name] = str(rel)
    found["README.md"] = "agent_protocol/README.md"
    return found


def _bounded_hit(text: str, token: str) -> bool:
    return re.search(
        rf"(?:^|{_TOKEN_BOUNDARY}){re.escape(token)}(?:$|{_TOKEN_BOUNDARY})",
        text,
    ) is not None


def _bare_section_hit(text: str, basename: str) -> bool:
    """Match section citations written with or without a file extension."""

    candidates = [basename]
    suffix = Path(basename).suffix
    if suffix in {".md", ".py"}:
        candidates.append(Path(basename).stem)
    return any(
        re.search(
            rf"(?:^|{_TOKEN_BOUNDARY}){re.escape(candidate)}\s+\u00a7",
            text,
        )
        is not None
        for candidate in candidates
    )


def extract_citations(text: str, *, catalog: dict[str, str]) -> set[str]:
    """Return normalized repo-relative citation targets from unfenced prose."""

    body = strip_fences(text)
    found: set[str] = set()


    for match in _PROTOCOL_PATH.finditer(body):
        found.add(match.group(0).replace("\\", "/"))

    for name in SCHEMA_FILES:
        if _bounded_hit(body, name):
            found.add(name)

    for name in SITUATIONAL_NAMES:
        if _bounded_hit(body, name):
            found.add(name)

    for basename, rel in catalog.items():
        if basename in SCHEMA_FILES or basename in SITUATIONAL_NAMES:
            continue
        if (
            _bare_section_hit(body, basename)
            or _bounded_hit(body, basename)
            or _bounded_hit(body, rel)
        ):
            found.add(rel)

    return found


def classify(rel: str, *, declared: frozenset[str], entry: str) -> str:
    if rel == entry or rel in declared:
        return "declared"
    if Path(rel).name in SITUATIONAL_NAMES:
        return "situational"
    return "undeclared"


@dataclass(frozen=True)
class ReachedFile:
    path: str
    kind: str
    size: int | None


@dataclass(frozen=True)
class RoleAudit:
    role: str
    entry: str
    reached: tuple[ReachedFile, ...]
    undeclared: tuple[str, ...]
    declared_bytes: int
    reached_bytes: int


def audit_role(
    role: str,
    *,
    root: Path,
    roles: dict[str, dict[str, object]] | None = None,
) -> RoleAudit:
    table = roles if roles is not None else ROLES
    if role not in table:
        raise KeyError(f"unknown role: {role!r}")
    spec = table[role]
    entry = str(spec["entry"])
    declared = frozenset(str(item) for item in spec["declared"])  # type: ignore[union-attr]
    catalog = protocol_relpaths(root)

    visited: set[str] = set()
    queue: list[str] = [entry]
    reached: dict[str, ReachedFile] = {}

    while queue:
        rel = queue.pop()
        if rel in visited:
            continue
        visited.add(rel)
        kind = classify(rel, declared=declared, entry=entry)
        path = root / rel
        size = path.stat().st_size if path.is_file() else None
        reached[rel] = ReachedFile(path=rel, kind=kind, size=size)

        # Report undeclared and situational targets; do not open them.
        # Never walk README: section 11 is a human copy of this map.
        if kind != "declared" or Path(rel).name == "README.md":
            continue
        if size is None:
            continue
        text = path.read_text(encoding="utf-8")
        for cite in extract_citations(text, catalog=catalog):
            if cite not in visited:
                queue.append(cite)

    ordered = tuple(sorted(reached.values(), key=lambda item: (item.kind, item.path)))
    undeclared = tuple(
        item.path for item in ordered if item.kind == "undeclared"
    )
    declared_bytes = sum(
        item.size or 0 for item in ordered if item.kind == "declared" and item.size
    )
    reached_bytes = sum(item.size or 0 for item in ordered if item.size)
    return RoleAudit(
        role=role,
        entry=entry,
        reached=ordered,
        undeclared=undeclared,
        declared_bytes=declared_bytes,
        reached_bytes=reached_bytes,
    )


def format_report(audit: RoleAudit) -> str:
    lines = [
        f"role: {audit.role}",
        f"entry: {audit.entry}",
        "reached:",
    ]
    for item in audit.reached:
        size = "-" if item.size is None else str(item.size)
        lines.append(f"  {item.kind:12} {size:>10}  {item.path}")
    lines.append(f"declared_bytes: {audit.declared_bytes}")
    lines.append(f"reached_bytes: {audit.reached_bytes}")
    if audit.undeclared:
        lines.append("undeclared:")
        for path in audit.undeclared:
            lines.append(f"  {path}")
    else:
        lines.append("undeclared: (none)")
    return "\n".join(lines)


def audit_roles(
    names: list[str],
    *,
    root: Path,
    roles: dict[str, dict[str, object]] | None = None,
) -> list[RoleAudit]:
    return [audit_role(name, root=root, roles=roles) for name in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a role's transitive protocol read-set and fail on undeclared citations."
    )
    parser.add_argument(
        "role",
        nargs="?",
        choices=sorted(ROLES),
        help="Role to audit. Omit with --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Audit every hardcoded role (worker, critic, worktree, accept-and-land, closeout, worker-area).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: derive from this file or cwd).",
    )
    args = parser.parse_args(argv)
    if args.all:
        names = list(ROLES)
    elif args.role:
        names = [args.role]
    else:
        parser.error("pass a role or --all")

    root = args.root.resolve() if args.root is not None else repo_root_from_here()
    audits = audit_roles(names, root=root)
    chunks = [format_report(item) for item in audits]
    print("\n\n".join(chunks))
    dirty = [item.role for item in audits if item.undeclared]
    if dirty:
        print("UNDECLARED in: " + ", ".join(dirty), file=sys.stderr)
        return 1
    print("CLEAN", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



