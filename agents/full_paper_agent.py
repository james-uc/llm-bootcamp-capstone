from agents.base_agent import BaseAgent
from .agent_factory import AgentFactory
from helpers import get_full_paper
import json

FULL_PAPER_PROMPT = """
You are an intelligent AI assistant that answers questions based on a set of research papers that you have access to. Your \
role answer the user's questions based on the full text from one ore more papers for the topic:

<papers>
%s
</papers>

Please respond to the user's query in a JSON-encoded object with the following format:
{
  "response": "Your response here",
  "paper_ids": ["paper_id1", "paper_id2"]
}

The paper_ids field is an optional field that should include the IDs of any papers whose text were used to help formulate the response.
"""


class FullPaperAgent(BaseAgent):
    """
    Subclass of BaseAgent specialized in answering questions based on full text of papers for a particular topic.
    """

    def __init__(
        self,
        name: str = "Full Paper Agent",
        litellm_model: str = "openai/gpt-4o",
        model_kwargs=None,
    ):
        """Initialize the full paper agent with default settings.

        Args:
            name: Name of the agent
            litellm_model: Model identifier for litellm
            model_kwargs: Optional generation parameters
        """
        super().__init__(
            name=name,
            system_prompt=FULL_PAPER_PROMPT,
            litellm_model=litellm_model,
            model_kwargs=model_kwargs,
        )

    async def load_full_papers(self, paper_ids: list):
        papers = []
        for paper_id in paper_ids:
            full_paper = await get_full_paper(paper_id)
            papers.append(full_paper)
        self.system_prompt = FULL_PAPER_PROMPT % (json.dumps(papers))


# Register the agent with the factory
AgentFactory.register(FullPaperAgent)
