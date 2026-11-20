"""Explicit local-only provision/import/reset commands; no automatic remote dispatch."""

import argparse
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from agent_workflow.application.admin.use_cases import AdminSeedCommand
from agent_workflow.application.shared.errors import (
    CorruptedPersistenceDataError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.workflow_identity import REPOSITORY as REPO
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec

try:
    from agent_workflow.composition.task_deps import (
        admin_seed_scope,
        task_authorization_scope,
    )
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    task_authorization_scope = None
    admin_seed_scope = None

BASE = "015ccc535b85ce1d0ad00da787b827970b9bf39a"
ROUND1 = "6352c8ff5ea83450a93cd1077d6447170c9dedaa"
ROUND2 = "ff1458fb98dec3a321ca9b96ed8723e40ba882a9"
PATHS = [
    "AGENTS.md",
    "docs/architecture.md",
    "docs/handoff.md",
    "docs/review-policy.md",
    "docs/tasks/TASK-001.md",
]


def historical_spec():
    text = (
        files("agent_workflow")
        .joinpath("resources/TASK-001-reference.md")
        .read_text(encoding="utf-8-sig")
    )
    ac = text.split("## Acceptance criteria\n", 1)[1].split("## Edge cases", 1)[0]
    return dict(
        task_id="TASK-001",
        title="Add idempotent order creation",
        description=text,
        acceptance_criteria=[
            line for line in ac.splitlines() if line and line[0].isdigit()
        ],
        definition_of_done=text.split("## Definition of Done", 1)[1].strip(),
        repository=REPO,
        branch="task/TASK-001",
        base_sha=BASE,
        requested_effort="high",
        risk_level="high",
        require_verified_effort=True,
        context_paths=PATHS,
        synthetic=False,
    )


def synthetic_spec():
    return dict(
        task_id="POC-REVIEW-001",
        title="SYNTHETIC POC-only baseline inspection",
        description="Disposable metadata exercise using a real baseline commit; not a real implementation assignment.",
        acceptance_criteria=[
            "Inspect the baseline order domain and confirm documented Decimal validation."
        ],
        definition_of_done="Record a synthetic review; do not modify code or merge.",
        repository=REPO,
        branch="poc/review-baseline",
        base_sha=BASE,
        requested_effort="medium",
        risk_level="low",
        require_verified_effort=False,
        context_paths=PATHS,
        synthetic=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["seed-history", "seed-synthetic", "reset-synthetic", "provision"],
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="deprecated no-op: local admin commands always use PostgreSQL",
    )
    args = parser.parse_args()
    if task_authorization_scope is None or admin_seed_scope is None:
        parser.error("PostgreSQL operations require the postgres extra")
    try:
        if args.action == "provision":
            if not args.file:
                parser.error(
                    "provision requires --file containing authorized TaskSpec JSON"
                )
            try:
                raw_spec = args.file.read_bytes()
            except OSError:
                parser.error("unable to read TaskSpec JSON file")
            command = ProvisionTaskSpec.model_validate_json(raw_spec).to_command()
            with task_authorization_scope() as use_case:
                use_case.execute(command)
        else:
            raw_spec = (
                historical_spec() if args.action == "seed-history" else synthetic_spec()
            )
            command = ProvisionTaskSpec.model_validate(raw_spec).to_command()
            with admin_seed_scope(args.action) as use_case:
                use_case.execute(AdminSeedCommand(command.spec))
    except ValidationError:
        parser.error("invalid TaskSpec JSON")
    except (
        ValueError,
        TimeoutError,
        PersistenceUnavailableError,
        CorruptedPersistenceDataError,
    ) as exc:
        parser.error(str(exc))
    print("Local operation completed; no review approval or AI effort claimed.")


if __name__ == "__main__":
    main()
