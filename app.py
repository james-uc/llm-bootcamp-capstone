import chainlit as cl
import openai
import base64
from langsmith.wrappers import wrap_openai
from langsmith import traceable
import weaviate
from weaviate.classes.init import Auth
from weaviate.classes.query import MetadataQuery
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
        return_properties=["text"],
        return_metadata=MetadataQuery(distance=True),
    )

    if len(similar_texts.objects) == 0:
        return None, None

    return len(similar_texts.objects), "\n\n---\n\n".join(
        [doc.properties["text"] for doc in similar_texts.objects]
    )


@cl.on_message
@traceable
async def on_message(message: cl.Message):
    # Maintain an array of messages in the user session
    message_history = cl.user_session.get(
        "message_history",
        [
            {
                "role": "system",
                "content": "You are an AI assistant. Please answer the user's questions to the best of your abilities. If you are provided with context following a user query, please provide a response drawing only from the context and the message history.",
            }
        ],
    )

    # Processing images exclusively
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
                    {
                        "type": "text",
                        "text": (
                            message.content
                            if message.content
                            else "What’s in this image?"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        )
    else:
        message_history.append({"role": "user", "content": message.content})

    rag_doc_count, rag_context = await check_rag(message.content)
    if rag_doc_count:
        message_history.append(
            {
                "role": "system",
                "content": f"Use only following context and the message history to respond to the most recent message. Context: {rag_context}",
            }
        )
        elements = [
            cl.Text(
                content=rag_context,
                display="inline",
            )
        ]
        await cl.Message(
            content=f"Additional context injected ({rag_doc_count} texts)",
            elements=elements,
        ).send()

    response_message = cl.Message(content="")
    await response_message.send()

    # Pass in the full message history for each request
    stream = await openai_client.chat.completions.create(
        messages=message_history, stream=True, **CONFIG["openai_client_kwargs"]
    )
    async for part in stream:
        if token := part.choices[0].delta.content or "":
            await response_message.stream_token(token)

    await response_message.update()

    # Record the AI's response in the history
    message_history.append({"role": "assistant", "content": response_message.content})
    cl.user_session.set("message_history", message_history)
