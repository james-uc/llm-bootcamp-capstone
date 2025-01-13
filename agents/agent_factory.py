class AgentFactory:
    _agents = {}  # Class variable to store agent types

    @classmethod
    def register(cls, agent_class):
        """Register an agent class with the factory"""
        cls._agents[agent_class.__name__] = agent_class

    @classmethod
    def create_agent(cls, agent_type: str):
        """Create an agent instance"""
        agent_class = cls._agents.get(agent_type)
        if agent_class:
            return agent_class()
        return None
