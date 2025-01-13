from agents.base_agent import BaseAgent
from .agent_factory import AgentFactory
from helpers import get_summaries
import json

SUMMARY_PROMPT = """
You are an intelligent AI assistant that answers questions based on a set of research papers that you have access to. Your \
role answer the user's questions based on the following summaries for the topic:

<summaries>
%s
</summaries>

Please respond to the user's query in a JSON-encoded object with the following format:
{
  "response": "Your response here",
  "paper_ids": ["paper_id1", "paper_id2"]
}

The paper_ids field should include the IDs of any papers whose summaries are part of the response.
"""


class SummaryAgent(BaseAgent):
    """
    Subclass of BaseAgent specialized in answering questions based on summaries of the papers for a particular topic.
    """

    def __init__(
        self,
        name: str = "Summary Agent",
        litellm_model: str = "openai/gpt-4o",
        model_kwargs=None,
    ):
        """Initialize the summary agent with default settings.

        Args:
            name: Name of the agent
            litellm_model: Model identifier for litellm
            model_kwargs: Optional generation parameters
        """
        super().__init__(
            name=name,
            system_prompt=SUMMARY_PROMPT,
            litellm_model=litellm_model,
            model_kwargs=model_kwargs,
        )

    async def load_summaries_for_topic(self, topic: str):
        summaries = await get_summaries(topic)
        self.system_prompt = SUMMARY_PROMPT % (json.dumps(summaries))


# Register the agent with the factory
AgentFactory.register(SummaryAgent)
