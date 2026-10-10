#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0.3"]
# ///
"""Move DC issues assigned to the current user into the DL project.

`plan` resolves the migration scope from live Jira and writes an editable YAML
plan. `apply` executes that plan through the Jira bulk-move API and appends
every old-to-new key to a JSONL ledger so the move stays auditable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from jira_client import JiraConfig, JiraError, request

SOURCE_PROJECT = "DC"
TARGET_PROJECT = "DL"
DEFAULT_PLAN = "jira_migration_plan.yaml"
DEFAULT_LEDGER = ".jira_migration.jsonl"

TARGET_TYPE_TASK = "12571"
TARGET_TYPE_EPIC = "12572"
SOURCE_TYPE_TASK = "10004"

# Target status id -> the source status ids it absorbs.
STATUS_MAP: dict[str, list[str]] = {
    "13685": ["10014"],  # BACKLOG    <- Backlog
    "13469": ["10296"],  # 해야 할 일   <- Selected for Development
    "13470": ["10297"],  # 진행 중     <- 진행 중
    "13471": ["10298"],  # 완료       <- 완료
}

# A target epic written as "@DC-974" means "the key DC-974 gets after it moves".
MOVED_PREFIX = "@"

# Ordered summary patterns mapping a source issue onto a DL epic. A `None`
# target moves the issue into DL without attaching it to any epic.
EPIC_RULES: tuple[tuple[str, str | None], ...] = (
    (r"정보계|humuson_id|전사통합id|ho id|고객사현황|표준테이블", "DL-49"),
    (r"모니터링|깨진 필드|운영|장애", "DL-33"),
    (r"\[AI\]|LLM|RAG", "DL-51"),  # 구 DC-974, 1단계에서 이관 완료
    (r"데이터랩|\[Catalog\]", None),
    (r"", "DL-31"),
)

SEARCH_FIELDS = "summary,status,issuetype,parent,subtasks,assignee"
POLL_INTERVAL_SECONDS = 2
POLL_LIMIT = 150

MigrationError = JiraError


@dataclass(frozen=True)
class Issue:
    """The subset of a Jira issue the migration reasons about."""

    key: str
    issue_id: str
    summary: str
    type_name: str
    is_subtask: bool
    status_name: str
    done: bool
    parent_key: str | None
    parent_type: str | None
    subtasks: tuple[dict[str, str], ...]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Issue:
        """Build an Issue from a Jira search result."""
        fields = raw["fields"]
        parent = fields.get("parent")
        return cls(
            key=raw["key"],
            issue_id=str(raw["id"]),
            summary=fields["summary"],
            type_name=fields["issuetype"]["name"],
            is_subtask=bool(fields["issuetype"].get("subtask")),
            status_name=fields["status"]["name"],
            done=fields["status"]["statusCategory"]["key"] == "done",
            parent_key=parent["key"] if parent else None,
            parent_type=(
                parent["fields"]["issuetype"]["name"] if parent else None
            ),
            subtasks=tuple(
                {
                    "key": sub["key"],
                    "id": str(sub["id"]),
                    "status_id": sub["fields"]["status"]["id"],
                }
                for sub in fields.get("subtasks") or ()
            ),
        )

    @property
    def is_epic(self) -> bool:
        """Whether this issue is an epic in the source project."""
        return self.type_name == "에픽"


def search_issues(config: JiraConfig, jql: str) -> list[Issue]:
    """Page through a JQL search and return every matching issue."""
    issues: list[Issue] = []
    token: str | None = None
    while True:
        query: dict[str, Any] = {
            "jql": jql,
            "maxResults": 100,
            "fields": SEARCH_FIELDS,
        }
        if token:
            query["nextPageToken"] = token
        page = request(
            config, "GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode(query)
        )
        issues.extend(Issue.from_api(raw) for raw in page.get("issues", []))
        token = page.get("nextPageToken")
        if not token:
            return issues


def select_scope(
    issues: list[Issue],
    excluded: frozenset[str] = frozenset(),
    include_done: bool = False,
) -> list[Issue]:
    """Pick the migration scope.

    By default this is the phase-1 scope: open issues, subtask parents, and
    epic children. `include_done` widens it to every issue, which is what the
    phase-2 sweep of already-completed work needs.

    Subtasks that travel with an in-scope parent are dropped, because Jira
    always moves a parent's subtasks along with it. Keys in `excluded` are held
    back along with the subtasks that would ride on them.
    """
    by_key = {issue.key: issue for issue in issues}
    selected = {
        issue.key
        for issue in issues
        if (include_done or not issue.done or issue.subtasks)
        and issue.key not in excluded
    }
    for issue in issues:
        if (
            issue.parent_type == "에픽"
            and issue.parent_key in selected
            and issue.key not in excluded
        ):
            selected.add(issue.key)

    scope: list[Issue] = []
    for key in selected:
        issue = by_key[key]
        # A subtask can never move on its own: it either rides along with its
        # parent or stays behind with it.
        if issue.is_subtask and issue.parent_key:
            continue
        scope.append(issue)
    return sorted(scope, key=lambda i: (not i.is_epic, i.key))


def find_orphan_subtasks(scope: list[Issue]) -> list[Issue]:
    """Return in-scope subtasks with no parent, which DL cannot accept as-is."""
    return [issue for issue in scope if issue.is_subtask and not issue.parent_key]


def classify_epic(summary: str, rules: tuple[tuple[str, str | None], ...]) -> str | None:
    """Map an issue summary onto a target epic using the first matching rule.

    Returns None when the matching rule parks the issue outside every epic.
    """
    for pattern, epic in rules:
        if re.search(pattern, summary, re.IGNORECASE):
            return epic
    raise MigrationError(f"no epic rule matched: {summary}")


def build_plan(
    scope: list[Issue], orphans: list[Issue], excluded: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Group the scope by target epic and render an editable plan mapping."""
    grouped: dict[tuple[str | None, str, bool], list[Issue]] = {}
    for issue in scope:
        target = None if issue.is_epic else classify_epic(issue.summary, EPIC_RULES)
        type_id = TARGET_TYPE_EPIC if issue.is_epic else TARGET_TYPE_TASK
        # An orphan subtask also changes hierarchy level on the way over, so it
        # travels alone and cannot drag a healthy group down with it.
        grouped.setdefault((target, type_id, issue.is_subtask), []).append(issue)

    def order(key: tuple[str | None, str, bool]) -> tuple[bool, bool, str]:
        """Epics move first, then ordinary groups, then the isolated ones."""
        target, type_id, isolated = key
        return (type_id != TARGET_TYPE_EPIC, isolated, target or "")

    groups = []
    for key in sorted(grouped, key=order):
        target, type_id, isolated = key
        members = grouped[key]
        groups.append(
            {
                "target_epic": target,
                "target_type_id": type_id,
                "isolated": isolated,
                "issues": [
                    {
                        "key": issue.key,
                        "id": issue.issue_id,
                        "summary": issue.summary,
                        "type": issue.type_name,
                        "status": issue.status_name,
                        "subtasks": list(issue.subtasks),
                    }
                    for issue in members
                ],
            }
        )

    return {
        "source_project": SOURCE_PROJECT,
        "target_project": TARGET_PROJECT,
        "generated_at": datetime.now(UTC).isoformat(),
        "status_map": STATUS_MAP,
        # Turning the bulk notification off needs global Jira admin rights.
        "send_bulk_notification": True,
        "excluded": list(excluded),
        "convert_to_task": [
            {
                "key": issue.key,
                "summary": issue.summary,
                "reason": "orphan subtask; DL subtasks require a parent",
            }
            for issue in orphans
        ],
        "groups": groups,
    }


def plan_totals(plan: dict[str, Any]) -> tuple[int, int]:
    """Count the top-level issues and the subtasks that ride along with them."""
    top = sum(len(group["issues"]) for group in plan["groups"])
    subtasks = sum(
        len(issue["subtasks"])
        for group in plan["groups"]
        for issue in group["issues"]
    )
    return top, subtasks


def render_plan(plan: dict[str, Any]) -> str:
    """Render the plan as a human-reviewable table."""
    lines = [f"{plan['source_project']} -> {plan['target_project']} 이관 계획", ""]
    for group in plan["groups"]:
        if group["target_type_id"] == TARGET_TYPE_EPIC:
            target = "(에픽 자체 이동)"
        else:
            target = group["target_epic"] or "(에픽 미지정)"
        note = " - 단독 이동" if group.get("isolated") else ""
        lines.append(f"[{target}]{note}")
        for issue in group["issues"]:
            rider = f" (+하위 {len(issue['subtasks'])})" if issue["subtasks"] else ""
            lines.append(
                f"  {issue['key']:8} {issue['type']:6} {issue['status']:7}"
                f" {issue['summary'][:46]}{rider}"
            )
        lines.append("")
    if plan["convert_to_task"]:
        lines.append("이동 전 유형 변환 (하위 작업 -> 작업):")
        lines += [
            f"  {item['key']} :: {item['reason']}" for item in plan["convert_to_task"]
        ]
        lines.append("")
    if plan.get("excluded"):
        lines.append("이번 단계에서 제외: " + ", ".join(plan["excluded"]))
        lines.append("")
    top, subtasks = plan_totals(plan)
    lines.append(f"합계: 최상위 {top}건 + 함께 이동하는 하위 작업 {subtasks}건")
    return "\n".join(lines)


def build_move_payload(
    issues: list[dict[str, Any]],
    type_id: str,
    status_map: dict[str, list[str]],
    parent: str | None,
    notify: bool = True,
) -> dict[str, Any]:
    """Build the bulk-move body for one target project/type/parent group.

    Suppressing the bulk notification needs a global Jira admin permission, so
    `notify` stays true unless the caller is entitled to turn it off.
    """
    mapping_key = f"{TARGET_PROJECT},{type_id}"
    if parent:
        mapping_key = f"{mapping_key},{parent}"
    return {
        "sendBulkNotification": notify,
        "targetToSourcesMapping": {
            mapping_key: {
                "inferClassificationDefaults": True,
                "inferFieldDefaults": True,
                "inferStatusDefaults": False,
                "inferSubtaskTypeDefault": True,
                "issueIdsOrKeys": [issue["key"] for issue in issues],
                "targetStatus": [{"statuses": status_map}],
            }
        },
    }


def resolve_parent(target: str | None, moved: dict[str, str]) -> str | None:
    """Resolve a "@DC-974" placeholder to the key that epic now has in DL."""
    if not target:
        return None
    if not target.startswith(MOVED_PREFIX):
        return target
    source_key = target[len(MOVED_PREFIX) :]
    if source_key not in moved:
        raise MigrationError(f"epic {source_key} must move before its children")
    return moved[source_key]


def convert_to_task(config: JiraConfig, key: str) -> None:
    """Turn an orphan subtask into a standard task in the source project."""
    request(
        config,
        "PUT",
        f"/rest/api/3/issue/{key}",
        {"fields": {"issuetype": {"id": SOURCE_TYPE_TASK}}},
    )


def await_task(config: JiraConfig, task_id: str) -> dict[str, Any]:
    """Poll a bulk-operation task until it leaves the running states."""
    for _ in range(POLL_LIMIT):
        progress = request(config, "GET", f"/rest/api/3/bulk/queue/{task_id}")
        if progress.get("status") not in {"ENQUEUED", "RUNNING"}:
            return progress
        time.sleep(POLL_INTERVAL_SECONDS)
    raise MigrationError(f"bulk task {task_id} did not finish in time")


def current_key(config: JiraConfig, issue_id: str) -> str:
    """Look up an issue's key by its id, which survives a project move."""
    return request(config, "GET", f"/rest/api/3/issue/{issue_id}?fields=summary")["key"]


def append_ledger(path: Path, record: dict[str, Any]) -> None:
    """Append one audit record to the migration ledger."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def target_status_for(source_status_id: str, status_map: dict[str, list[str]]) -> str | None:
    """Find the target status a source status maps onto, if any."""
    for target, sources in status_map.items():
        if source_status_id in sources:
            return target
    return None


def restore_subtask_statuses(
    config: JiraConfig,
    subtasks: list[dict[str, str]],
    status_map: dict[str, list[str]],
) -> dict[str, str]:
    """Put moved subtasks back on the status they held in the source project.

    Jira refuses a status mapping for subtasks that ride along with a parent
    (`inferSubtaskTypeDefault`), so it drops them on the target workflow's
    initial status. This walks them back to where they were.
    """
    restored: dict[str, str] = {}
    for subtask in subtasks:
        wanted = target_status_for(subtask["status_id"], status_map)
        if not wanted:
            continue
        key = current_key(config, subtask["id"])
        current = request(config, "GET", f"/rest/api/3/issue/{key}?fields=status")
        if current["fields"]["status"]["id"] == wanted:
            continue
        available = request(config, "GET", f"/rest/api/3/issue/{key}/transitions")
        match = [t for t in available["transitions"] if t["to"]["id"] == wanted]
        if not match:
            sys.stderr.write(f"WARNING: {key} 상태 복원 불가 (전이 없음)\n")
            continue
        request(
            config,
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            {"transition": {"id": match[0]["id"]}},
        )
        restored[key] = wanted
    return restored


def move_issue(
    config: JiraConfig,
    issue: dict[str, Any],
    type_id: str,
    status_map: dict[str, list[str]],
    parent: str | None,
    ledger: Path,
    notify: bool = True,
) -> str:
    """Move one top-level issue with its subtasks and return its new key."""
    payload = build_move_payload([issue], type_id, status_map, parent, notify)
    submitted = request(config, "POST", "/rest/api/3/bulk/issues/move", payload)
    task_id = submitted.get("taskId")
    if not task_id:
        raise MigrationError(f"bulk move returned no taskId: {submitted}")

    progress = await_task(config, task_id)
    if progress.get("status") != "COMPLETE":
        raise MigrationError(
            f"{issue['key']} 이동 실패 ({progress.get('status')}): "
            f"{progress.get('failedAccessibleIssues')}"
        )

    new_key = current_key(config, issue["id"])
    restored = restore_subtask_statuses(config, issue.get("subtasks") or [], status_map)
    append_ledger(
        ledger,
        {
            "at": datetime.now(UTC).isoformat(),
            "source": issue["key"],
            "target": new_key,
            "resolved_parent": parent,
            "task_id": task_id,
            "restored_subtask_statuses": restored,
            "total_issue_count": progress.get("totalIssueCount"),
        },
    )
    return new_key


def apply_plan(
    config: JiraConfig, plan: dict[str, Any], ledger: Path, dry_run: bool
) -> dict[str, str]:
    """Run the conversions and every group move described by the plan."""
    for item in plan.get("convert_to_task") or ():
        if dry_run:
            sys.stdout.write(f"[dry-run] convert {item['key']} -> 작업\n")
            continue
        try:
            convert_to_task(config, item["key"])
        except MigrationError as exc:
            # Company-managed Jira rejects a subtask-to-task edit over REST. The
            # bulk move retargets the type anyway, so this stays advisory.
            sys.stderr.write(f"WARNING: {item['key']} 유형 변환 실패, 이동에 위임: {exc}\n")
        else:
            sys.stdout.write(f"converted {item['key']} -> 작업\n")

    status_map = plan.get("status_map") or STATUS_MAP
    notify = plan.get("send_bulk_notification", True)
    moved: dict[str, str] = {}
    for group in plan["groups"]:
        parent = resolve_parent(group["target_epic"], moved)
        type_id = group["target_type_id"]
        for issue in group["issues"]:
            if dry_run:
                payload = build_move_payload(
                    [issue], type_id, status_map, parent, notify
                )
                sys.stdout.write(
                    json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
                )
                # Stand in for the key a real move would mint, so that later
                # groups can still resolve their "@DC-974" style parents.
                moved[issue["key"]] = f"<{issue['key']} 이동 후 키>"
                continue
            new_key = move_issue(
                config, issue, type_id, status_map, parent, ledger, notify
            )
            moved[issue["key"]] = new_key
            sys.stdout.write(f"moved {issue['key']} -> {new_key}\n")
    return moved


def run_plan(args: argparse.Namespace) -> int:
    """Resolve the scope from Jira and write the editable plan file."""
    config = JiraConfig.load(Path(args.config))
    jql = f"project = {SOURCE_PROJECT} AND assignee = currentUser()"
    issues = search_issues(config, jql)
    excluded = tuple(args.exclude)
    scope = select_scope(issues, frozenset(excluded), args.include_done)
    plan = build_plan(scope, find_orphan_subtasks(scope), excluded)

    output = Path(args.plan)
    output.write_text(
        yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    sys.stdout.write(render_plan(plan) + f"\n\n계획 파일: {output}\n")
    return 0


def run_apply(args: argparse.Namespace) -> int:
    """Execute a previously reviewed plan file."""
    plan = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
    config = JiraConfig.load(Path(args.config), allow_legacy_token=args.dry_run)
    moved = apply_plan(config, plan, Path(args.ledger), args.dry_run)
    if not args.dry_run:
        sys.stdout.write(f"\n이관 완료: 최상위 {len(moved)}건 (원장: {args.ledger})\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Move DC issues assigned to you into the DL project."
    )
    parser.add_argument("--config", default="config.yaml", help="YAML config path.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    planner = subcommands.add_parser("plan", help="Write an editable migration plan.")
    planner.add_argument("--plan", default=DEFAULT_PLAN, help="Plan file to write.")
    planner.add_argument(
        "--include-done",
        action="store_true",
        help="Also migrate issues that are already complete (phase 2).",
    )
    planner.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="ISSUE",
        help="Hold an issue (and its subtasks) back from this phase.",
    )
    planner.set_defaults(handler=run_plan)

    applier = subcommands.add_parser("apply", help="Execute a migration plan.")
    applier.add_argument("--plan", default=DEFAULT_PLAN, help="Plan file to read.")
    applier.add_argument("--ledger", default=DEFAULT_LEDGER, help="Audit ledger path.")
    applier.add_argument(
        "--dry-run", action="store_true", help="Print payloads without sending them."
    )
    applier.set_defaults(handler=run_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the migration command."""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (MigrationError, OSError, ValueError, yaml.YAMLError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
