from dotenv import load_dotenv
import chainlit as cl
import litellm
from agents.supervisor_agent import SupervisorAgent
from langsmith import traceable
from typing import AsyncGenerator
import base64

load_dotenv(override=True)

litellm.success_callback = ["langsmith"]

# Available model configurations
MODEL_OPENAI_GPT4 = "openai/gpt-4o"
MODEL_ANTHROPIC_CLAUDE = "anthropic/claude-3-5-sonnet-latest"


async def on_tag_start(
    parent_id: str, tag_name: str, stream: AsyncGenerator[str, None]
):
    # Create step with parent message ID
    step = cl.Step(name=tag_name, parent_id=parent_id)
    await step.send()

    content = ""
    async for token in stream:
        content += token
        await step.stream_token(token)

    await step.update()
    return step


async def on_message_start(id: str, stream: AsyncGenerator[str, None]):
    message = cl.Message(id=id, content="")
    await message.send()

    content = ""
    async for token in stream:
        content += token
        await message.stream_token(token)

    await message.update()
    return message


@traceable
@cl.on_chat_start
def on_chat_start():
    model_kwargs = {"temperature": 0.1, "max_tokens": 8192}

    agent = SupervisorAgent(litellm_model=MODEL_OPENAI_GPT4, model_kwargs=model_kwargs)
    cl.user_session.set("agent", agent)


@cl.on_message
@traceable
async def on_message(message: cl.Message):
    message_history = cl.user_session.get("message_history", [])
    agent = cl.user_session.get("agent")

    images = (
        [file for file in message.elements if "image" in file.mime]
        if message.elements
        else []
    )
    if images:
        # Read the first image and encode it to base64
        with open(images[0].path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode("utf-8")
        message_history.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": message.content},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        )
    else:
        message_history.append({"role": "user", "content": message.content})

    full_response = ""
    async for token in agent.react_to(
        message_history, on_tag_start=on_tag_start, on_message_start=on_message_start
    ):
        full_response += token

    cl.user_session.set("message_history", message_history)


if __name__ == "__main__":
    cl.main()
