import chainlit as cl
import openai
from langsmith.wrappers import wrap_openai
from langsmith import traceable
import weaviate
from weaviate.classes.init import Auth
from weaviate.classes.query import MetadataQuery
import json
from helpers import get_topics, get_summaries, get_full_paper, extract_tag_content
from prompts import BASE_PROMPT
from config import CONFIG

openai_client = wrap_openai(
    openai.AsyncClient(
        api_key=CONFIG["openai_api_key"], base_url=CONFIG["openai_endpoint_url"]
    )
)

weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=CONFIG["wcd_url"],
    auth_credentials=Auth.api_key(CONFIG["wcd_api_key"]),
)

weaviate_collection = weaviate_client.collections.get(CONFIG["vector_store_name"])


async def check_rag(query):
    query_embedding_resp = await openai_client.embeddings.create(
        model=CONFIG["embed_model"], input=query
    )
    query_embedding = query_embedding_resp.data[0].embedding

    similar_texts = weaviate_collection.query.near_vector(
        near_vector=query_embedding,
        certainty=0.78,
        limit=10,
        return_properties=["text", "file_name"],
        return_metadata=MetadataQuery(distance=True),
    )

    return [doc.properties for doc in similar_texts.objects]


async def get_system_prompt(query, topic, full_paper_id):
    user_selections = cl.user_session.get("user_selections", {})
    current_full_paper_id = user_selections.get("full_paper_id", None)
    current_full_paper = user_selections.get("current_full_paper", None)
    current_topic = user_selections.get("topic", None)
    current_summaries = user_selections.get("summaries", None)
    current_rag_docs = user_selections.get("rag_docs", None)

    if topic and topic != current_topic:
        if topic_summaries := get_summaries(topic):
            user_selections["topic"] = topic
            current_summaries = user_selections["summaries"] = topic_summaries

    if full_paper_id and full_paper_id != current_full_paper_id:
        if full_paper := get_full_paper(full_paper_id):
            user_selections["full_paper_id"] = full_paper_id
            current_full_paper = user_selections["current_full_paper"] = full_paper

    system_prompt = BASE_PROMPT
    topics = get_topics()
    system_prompt += f"\n\nThe following are the topics you can discuss: <topics>{json.dumps(topics)}</topics>"

    if current_topic:
        system_prompt += f"\n\nYou are currently discussing the topic: {current_topic}"
    else:
        system_prompt += "\n\nPlease prompt the user to select among one of the topics you are able to discuss."

    if current_summaries:
        system_prompt += f"\n\nThe following are summaries of papers you can get the full text for: <paper_summaries>{json.dumps(current_summaries)}</paper_summaries>"
    if current_full_paper:
        system_prompt += f"\n\nYou currently have the full text for the following full paper: <full_paper_text>{json.dumps(current_full_paper)}</full_paper_text>"

    if rag_docs := await check_rag(query):
        current_rag_docs = user_selections["rag_docs"] = rag_docs
    if current_rag_docs:
        system_prompt += f"\n\nPrioritize the following verbatim paper passages to respond to the most recent message: <verbatim_paper_passages>{json.dumps(rag_docs)}</verbatim_paper_passages>"

    cl.user_session.set("user_selections", user_selections)
    return system_prompt


@cl.on_message
@traceable
async def on_message(message: cl.Message):

    message_history = cl.user_session.get(
        "message_history",
        [],
    )

    system_prompt = await get_system_prompt(message.content, None, None)

    message_history.insert(0, {"role": "system", "content": system_prompt})
    message_history.append({"role": "user", "content": message.content})

    response_message = cl.Message(content="")
    await response_message.send()

    stream = await openai_client.chat.completions.create(
        messages=message_history, stream=True, **CONFIG["openai_client_kwargs"]
    )
    async for part in stream:
        if token := part.choices[0].delta.content or "":
            await response_message.stream_token(token)

    await response_message.update()
    message_history.append({"role": "assistant", "content": response_message.content})

    while function_call := extract_tag_content(
        response_message.content, "function_call"
    ):
        function_call = json.loads(function_call)
        function_name = function_call["name"]
        function_args = function_call.get("arguments", {})

        if function_name == "get_full_text":
            system_prompt = await get_system_prompt(
                message.content, None, function_args["id"]
            )
            message_history[0] = {"role": "system", "content": system_prompt}

        elif function_name == "change_topic":
            system_prompt = await get_system_prompt(
                message.content, function_args["topic"], None
            )
            message_history[0] = {"role": "system", "content": system_prompt}

        else:
            break

        response_message = cl.Message(content="")
        await response_message.send()

        stream = await openai_client.chat.completions.create(
            messages=message_history, stream=True, **CONFIG["openai_client_kwargs"]
        )
        async for part in stream:
            if token := part.choices[0].delta.content or "":
                await response_message.stream_token(token)

        await response_message.update()
        message_history.append(
            {"role": "assistant", "content": response_message.content}
        )

    # Record the AI's response in the history
    message_history.append({"role": "assistant", "content": response_message.content})
    cl.user_session.set("message_history", message_history[1:])
