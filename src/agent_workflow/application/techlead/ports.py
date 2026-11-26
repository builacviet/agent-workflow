"""Layer: Application
Responsibility: Define the Tech Lead outbound capability contracts.
Dependencies: Standard library typing only.
"""

from typing import Protocol


class ITechLeadGitHub(Protocol):
    async def identity(self) -> dict: ...

    async def get_pr(self, pr_number: int) -> dict: ...

    async def comment_review(
        self, pr_number: int, expected_head_sha: str, body: str
    ) -> dict: ...


class ITechLeadNotion(Protocol):
    async def identity(self) -> dict: ...

    async def get_task(self, page_id: str) -> dict: ...

    async def comment(self, page_id: str, body: str) -> dict: ...
