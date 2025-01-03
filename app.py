import chainlit as cl
import openai
from langsmith.wrappers import wrap_openai
from langsmith import traceable
import weaviate
from weaviate.classes.init import Auth
from weaviate.classes.query import MetadataQuery
import json
from helpers import get_summaries, get_full_text, extract_tag_content
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
        certainty=0.6,
        limit=10,
        return_properties=["text", "file_name"],
        return_metadata=MetadataQuery(distance=True),
    )

    return [doc.properties for doc in similar_texts.objects]


SYSTEM_PROMPT = BASE_PROMPT
CURRENT_PAPER_ID = None


async def update_system_prompt(query, full_paper_id):
    global SYSTEM_PROMPT
    global CURRENT_PAPER_ID

    paper_summaries = get_summaries()
    SYSTEM_PROMPT = f"{BASE_PROMPT}\n\n You have access to the following papers: <available_papers>{json.dumps(paper_summaries)}</available_papers>"

    if full_paper_id:
        CURRENT_PAPER_ID = full_paper_id

    if CURRENT_PAPER_ID:
        full_text = get_full_text(CURRENT_PAPER_ID)
        SYSTEM_PROMPT += f"The full text for paper {full_paper_id} is: <paper_full_text><paper_id>{full_paper_id}</paper_id>{full_text}</paper_full_text>"

    rag_docs = await check_rag(query)
    if rag_docs:
        SYSTEM_PROMPT += f"Prioritize the following paper passages to respond to the most recent message: <available_paper_passages>{json.dumps(rag_docs)}</available_paper_passages>"

    return SYSTEM_PROMPT


@cl.on_message
@traceable
async def on_message(message: cl.Message):

    message_history = cl.user_session.get(
        "message_history",
        [],
    )

    system_prompt = await update_system_prompt(message.content, None)

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
            system_prompt = await update_system_prompt(
                message.content, function_args["id"]
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
