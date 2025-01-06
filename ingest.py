from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.vector_stores.weaviate import WeaviateVectorStore
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.readers.file.docs.base import PDFReader
import weaviate
from weaviate.classes.init import Auth
import openai
import sqlite3
import os
import sys
from config import CONFIG

Settings.embed_model = OpenAIEmbedding(model=CONFIG["embed_model"])

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} <directory> <topic_id>")
else:
    directory = sys.argv[1]
    topic_id = sys.argv[2]

#
# Read the docs in the papers folder
#
try:
    file_extractor = {".pdf": PDFReader(return_full_document=True)}
    docs = SimpleDirectoryReader(
        directory, filename_as_id=True, file_extractor=file_extractor
    ).load_data()
except Exception as e:
    print(f"Error loading the documents: {e}")
    docs = []

#
# Store the full-text and a summary of each paper into SQLite
#
conn = sqlite3.connect(CONFIG["database_path"])
cursor = conn.cursor()

openai_client = openai.Client(
    api_key=CONFIG["openai_api_key"], base_url=CONFIG["openai_endpoint_url"]
)

for doc in docs:
    doc.id_ = os.path.relpath(doc.id_.replace("_part_0", ""), directory)

    print("Processing document:", doc.id_)
    try:
        message_history = [
            {
                "role": "system",
                "content": "You are a research paper summarizer. Please provide a concise summary of the text of a research paper provided by the user.",
            },
            {
                "role": "user",
                "content": doc.text,
            },
        ]
        openai_response = openai_client.chat.completions.create(
            messages=message_history, **CONFIG["openai_client_kwargs"]
        )

        cursor.execute(
            "INSERT INTO papers (id, full_text, summary, topic_id) VALUES (?, ?, ?, ?)",
            (doc.id_, doc.text, openai_response.choices[0].message.content, topic_id),
        )
        conn.commit()

    except Exception as e:
        print(f"Error processing document {doc.id_}: {e}")

conn.close()

exit()
#
# Index the papers into Weaviate for RAG
#
parser = SimpleNodeParser()
nodes = parser.get_nodes_from_documents(docs)

weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=CONFIG["wcd_url"],
    auth_credentials=Auth.api_key(CONFIG["wcd_api_key"]),
)
try:
    vector_store = WeaviateVectorStore(
        weaviate_client=weaviate_client, index_name=CONFIG["vector_store_name"]
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)
finally:
    weaviate_client.close()
