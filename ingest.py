from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.vector_stores.weaviate import WeaviateVectorStore
from llama_index.embeddings.openai import OpenAIEmbedding
import weaviate
from weaviate.classes.init import Auth
from config import CONFIG

Settings.embed_model = OpenAIEmbedding(model=CONFIG["embed_model"])

# Ingest the docs in the papers folder
try:
    docs = SimpleDirectoryReader("./papers").load_data()
except Exception as e:
    print(f"Error loading the documents: {e}")
    docs = []

print(f"Loaded {len(docs)} documents")

parser = SimpleNodeParser()
nodes = parser.get_nodes_from_documents(docs)

# connect to weaviate
client = weaviate.connect_to_weaviate_cloud(
    cluster_url=CONFIG["wcd_url"],
    auth_credentials=Auth.api_key(CONFIG["wcd_api_key"]),
)

# construct vector store
vector_store = WeaviateVectorStore(
    weaviate_client=client, index_name=CONFIG["vector_store_name"]
)

# setting up the storage for the embeddings
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# set up the index
print("Setting up the index")
index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)

client.close()
