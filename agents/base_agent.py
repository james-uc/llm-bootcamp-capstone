from typing import AsyncGenerator, Callable
import litellm
from enum import Enum
import asyncio
import json
import uuid
from .agent_factory import AgentFactory


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        litellm_model: str = None,
        model_kwargs=None,
    ):
        """Initialize an agent with a name, model name, system prompt, and optional functions.

        Args:
            name: Name of the agent
            litellm_model: Model identifier for litellm
            system_prompt: The system prompt to guide agent behavior
            model_kwargs: Optional generation parameters like temperature
        """
        self.name = name
        self.system_prompt = system_prompt
        self.model = litellm_model or "openai/gpt-4o"
        self.model_kwargs = model_kwargs or {"temperature": 0.2, "max_tokens": 8192}
        self._active_tasks = set()

    async def react_to(
        self,
        messages: list,
        on_tag_start: Callable[[str, str, AsyncGenerator[str, None]], None],
        on_message_start: Callable[[str, AsyncGenerator[str, None]], None],
    ) -> AsyncGenerator[str, None]:
        """Get next response as a stream, processing any XML tags encountered.

        Args:
            messages: The conversation history
            on_tag_start: Callback for tag processing
            on_message_start: Callback for message processing
        """
        # Store messages for function access
        self._current_messages = messages

        while True:
            function_calls = []  # Array to store function calls
            agent_delegation_request = None  # Single delegation request
            message_id = str(uuid.uuid4())

            async def handle_tag(
                message_id: str, tag_name: str, stream: AsyncGenerator[str, None]
            ):
                nonlocal function_calls, agent_delegation_request

                # Create a forwarding stream for on_tag_start
                queue = await self._create_stream(message_id, tag_name, on_tag_start)

                # Consume and forward tokens
                content = ""
                async for token in stream:
                    content += token
                    await queue.put(token)

                # Signal end of stream
                await queue.put(None)

                # If this is a function call, store it
                if tag_name == "function_call":
                    # Strip the function_call tags from content before storing
                    content = (
                        content.replace("<function_call>", "")
                        .replace("</function_call>", "")
                        .strip()
                    )
                    function_calls.append(content)
                # If this is an agent delegation, store it
                elif tag_name == "delegate_agent":
                    # Strip the delegate_agent tags from content before storing
                    content = (
                        content.replace("<delegate_agent>", "")
                        .replace("</delegate_agent>", "")
                        .strip()
                    )
                    agent_delegation_request = content

            # Get the next response and accumulate the full message
            full_response = ""
            async for token in self.next_response(
                messages,
                message_id,
                on_tag_start=handle_tag,
                on_message_start=on_message_start,
            ):
                full_response += token
                yield token

            # Store the complete response in message history
            messages.append({"role": "assistant", "content": full_response})

            # After response is complete, execute any collected function calls
            for function_call in function_calls:
                # Execute the function call
                result = await self._execute_function(function_call)

                # Stream the result as a tagged event
                tagged_result = f"<function_result>{result}</function_result>"
                await self._stream_tagged_content(
                    message_id, "function_result", result, on_tag_start
                )
                yield tagged_result

                # Add to message history
                messages.append({"role": "user", "content": tagged_result})

            # Handle agent delegation if requested
            if agent_delegation_request:
                delegation_result = ""
                try:
                    # Parse the delegation request
                    delegation = json.loads(agent_delegation_request)

                    agent_name = delegation.get("name")
                    instructions = delegation.get("instructions")
                    topic = delegation.get("topic", None)
                    paper_ids = delegation.get("paper_ids", [])

                    # Create the delegated agent
                    delegated_agent = AgentFactory.create_agent(agent_name)

                    if not delegated_agent:
                        delegation_result = f"ERROR: Could not create agent of type {agent_name}. Do not retry delegation."
                    else:
                        # Create a fresh message list with just the instruction
                        delegated_messages = [{"role": "user", "content": instructions}]

                        if topic:
                            print(f"Loading summaries for topic: {topic}")
                            await delegated_agent.load_summaries_for_topic(topic)

                        if paper_ids:
                            print(f"Loading full papers: {paper_ids}")
                            await delegated_agent.load_full_papers(paper_ids)

                        # Call react_to with our existing callbacks
                        async for token in delegated_agent.react_to(
                            delegated_messages,
                            on_tag_start=on_tag_start,
                            on_message_start=None,
                        ):
                            yield token

                        # Store the successful result
                        delegation_result = delegated_messages[-1]["content"]

                except json.JSONDecodeError:
                    delegation_result = "ERROR: Invalid agent delegation format. Do not retry delegation."
                except Exception as e:
                    delegation_result = f"ERROR: Failed to execute agent delegation: {str(e)}. Do not retry delegation."
                finally:
                    # Stream the final result or error as a tagged event
                    tagged_result = f"<delegate_agent_result>{delegation_result}</delegate_agent_result>"
                    await self._stream_tagged_content(
                        message_id,
                        "delegate_agent_result",
                        delegation_result,
                        on_tag_start,
                    )
                    yield tagged_result

                    # Add only the result to message history
                    messages.append({"role": "user", "content": tagged_result})

            if not function_calls and not agent_delegation_request:
                break

            function_calls.clear()  # Clear the array after processing
            agent_delegation_request = None

        pending = [t for t in self._active_tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def next_response(
        self,
        messages: list,
        message_id: str,
        on_tag_start: Callable[[str, str, AsyncGenerator[str, None]], None],
        on_message_start: Callable[[str, AsyncGenerator[str, None]], None],
    ) -> AsyncGenerator[str, None]:
        """Get next response as a stream, processing any XML tags encountered.
        The returned stream includes all tokens from the response, including XML tags.

        Args:
            messages: The conversation history
            on_tag_start: Callback function with parameters:
                - tag_name (str): Name of the XML tag encountered (e.g. "thought_process")
                - tag_stream (AsyncGenerator[str, None]): Async generator that yields tokens
                  contained within the tag
            on_message_start: Callback function with parameters:
                - message_stream (AsyncGenerator[str, None]): Async generator that yields
                  tokens from the regular message content (excluding tags)

        Yields:
            All tokens from the response stream (unfiltered)
        """

        class StreamMode(Enum):
            NORMAL = "normal"
            COLLECTING_TAG = "collecting_tag"
            IN_TAG = "in_tag"

        # Create a copy and remove all system messages
        messages = [msg for msg in messages if msg["role"] != "system"]

        # Insert system messages
        messages.insert(0, {"role": "system", "content": self.system_prompt})

        response = await litellm.acompletion(
            model=self.model, messages=messages, stream=True, **self.model_kwargs
        )

        message_queue = None
        tag_queue = None
        mode = StreamMode.NORMAL
        current_tag = []
        current_tag_name = None
        tag_chunk_buffer = ""
        full_tag_buffer = ""

        async for chunk in response:
            if token := chunk.choices[0].delta.content or "":
                yield token

                message_buffer = ""

                for char in token:
                    if mode == StreamMode.NORMAL:
                        if char == "<":
                            mode = StreamMode.COLLECTING_TAG
                            current_tag = ["<"]
                            tag_chunk_buffer = "<"
                            full_tag_buffer = "<"
                        else:
                            message_buffer += char

                    elif mode == StreamMode.COLLECTING_TAG:
                        current_tag.append(char)
                        tag_chunk_buffer += char
                        full_tag_buffer += char
                        if char == ">":
                            tag_text = "".join(current_tag)
                            current_tag_name = tag_text[1:-1]
                            mode = StreamMode.IN_TAG
                            if tag_queue is not None:
                                await tag_queue.put(None)
                            tag_queue = await self._create_stream(
                                message_id, current_tag_name, on_tag_start
                            )
                            await tag_queue.put(tag_chunk_buffer)
                            tag_chunk_buffer = ""

                    elif mode == StreamMode.IN_TAG:
                        tag_chunk_buffer += char
                        full_tag_buffer += char

                        if full_tag_buffer.endswith(f"</{current_tag_name}>"):
                            await tag_queue.put(tag_chunk_buffer)
                            mode = StreamMode.NORMAL
                            current_tag_name = None
                            tag_chunk_buffer = ""
                            full_tag_buffer = ""
                            await tag_queue.put(None)
                            tag_queue = None

                # Send any accumulated message content to the message queue
                if message_buffer:
                    if message_queue is None:
                        message_queue = await self._create_stream(
                            message_id, None, on_message_start
                        )
                    await message_queue.put(message_buffer)
                    message_buffer = ""

                # Send any accumulated tag chunk content to the tag queue
                if tag_chunk_buffer and tag_queue is not None:
                    await tag_queue.put(tag_chunk_buffer)
                    tag_chunk_buffer = ""

        if message_queue is not None:
            await message_queue.put(None)

    async def _execute_function(self, function_call_str: str) -> str:
        """Execute a function call and return its result.

        Args:
            function_call_str: The function call in JSON format as a string

        Returns:
            The result of the function call as a string
        """
        try:
            # Parse the function call JSON
            function_call = json.loads(function_call_str)

            # Get the function name and arguments
            function_name = function_call.get("name")
            function_args = function_call.get("arguments", {})

            # Use dynamic method lookup
            if hasattr(self, function_name):
                func = getattr(self, function_name)
                result = func(**function_args)
                return result
            else:
                return f"Error: Function '{function_name}' not implemented"

        except json.JSONDecodeError:
            return "Error: Invalid function call format"
        except Exception as e:
            return f"Error executing function: {str(e)}"

    async def _stream_tagged_content(
        self,
        message_id: str,
        tag_name: str,
        content: str,
        on_tag_start: Callable[[str, AsyncGenerator[str, None]], None],
    ) -> None:
        """Stream content wrapped in XML tags, firing the on_tag_start event.

        Args:
            tag_name: Name of the tag to wrap content in
            content: The content to wrap and stream
            on_tag_start: Callback for tag processing
        """
        # Create a queue and stream for the tagged content
        queue = await self._create_stream(message_id, tag_name, on_tag_start)

        # Send the content all at once
        await queue.put(content)

        # Signal end of stream
        await queue.put(None)

    async def _create_stream(
        self,
        message_id: str,
        stream_name: str | None,
        on_stream_start: Callable[..., None] | None = None,
    ) -> asyncio.Queue:
        """Creates a stream for content and sets up its processing.

        Args:
            stream_name: Name of the stream (for tags) or None for message stream
            on_stream_start: Optional callback to handle the stream. If provided, will be called with
                (stream_name, stream) for tags or (stream) for messages

        Returns:
            Queue for sending content to the stream
        """
        queue = asyncio.Queue()

        if on_stream_start is not None:

            async def stream() -> AsyncGenerator[str, None]:
                try:
                    while True:
                        chunk = await queue.get()
                        if chunk is None:  # None signals end of stream
                            break
                        yield chunk
                finally:
                    pass

            task = asyncio.create_task(
                on_stream_start(message_id, stream_name, stream())
                if stream_name is not None
                else on_stream_start(message_id, stream())
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

        return queue
