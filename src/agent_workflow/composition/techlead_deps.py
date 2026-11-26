"""Layer: Composition
Responsibility: Assemble dedicated Tech Lead outbound adapters.
Dependencies: Application ports, configuration and Infrastructure adapters.
"""

from agent_workflow.app_config import TechLeadGitHubConfig, TechLeadNotionConfig
from agent_workflow.application.techlead.ports import ITechLeadGitHub, ITechLeadNotion
from agent_workflow.infrastructure.techlead.github import TechLeadGitHub
from agent_workflow.infrastructure.techlead.notion import TechLeadNotion


def create_techlead_github(config: TechLeadGitHubConfig) -> ITechLeadGitHub:
    return TechLeadGitHub(config)


def create_techlead_notion(config: TechLeadNotionConfig) -> ITechLeadNotion:
    return TechLeadNotion(config)
