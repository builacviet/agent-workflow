"""Layer: Interfaces/MCP
Responsibility: Map review and assignment DTOs to shared MCP and Worker payloads.
Dependencies: Application review and assignment outputs.
"""

import json
from dataclasses import asdict
from urllib.parse import quote

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextOutput,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    CoderAssignmentOutput,
)


def review_context_response(output: GetReviewContextOutput) -> dict:
    task = asdict(output.task)
    # The MCP contract flattens the original task spec, including stored fields
    # absent from the typed workflow task projection.
    spec = json.loads(task.pop("spec_json"))
    task.pop("context_paths")
    history = []
    for review in output.previous_reviews:
        record = asdict(review)
        record["prior_resolutions"] = dict(review.prior_resolutions)
        record["findings"] = list(record["findings"])
        history.append(record)

    root = f"https://github.com/{output.task.repository}"
    head = output.task.current_head_sha
    prior = output.previous_reviewed_sha
    return {
        **spec,
        **task,
        "review_round": output.review_round,
        "rounds_remaining": output.rounds_remaining,
        "previous_reviews": history,
        "previous_reviewed_sha": prior,
        "previous_verdict": output.previous_verdict,
        "github_references": {
            "complete_diff": f"{root}/compare/{output.task.base_sha}...{head}"
            if head
            else None,
            "correction_diff": f"{root}/compare/{prior}...{head}"
            if prior and head
            else None,
            "context_files": {
                path: f"{root}/blob/{head}/{quote(path, safe='/')}"
                for path in output.task.context_paths
            }
            if head
            else {},
        },
        "events": [asdict(event) for event in output.events],
    }


def coder_assignment_response(output: CoderAssignmentOutput) -> dict:
    response = review_context_response(output.context)
    response["unresolved_findings"] = [
        {**asdict(finding), "resolution_status": status}
        for finding, status in output.unresolved_findings
    ]
    return response
