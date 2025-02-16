from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from PyPDF2 import PdfReader
from langchain.text_splitter import CharacterTextSplitter
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import Pinecone as PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
import os
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth import login, authenticate, logout
from dotenv import load_dotenv
from langchain.schema import Document
# from langchain.llms import Ollama  # Use LangChain's Ollama LLM integration
from transformers import pipeline
import json
import ijson
import sys


# Replace Ollama with Hugging Face LLM
llm = pipeline("text-generation", model="gpt2")

# Load environment variables from .env file
load_dotenv()

# Initialize the Ollama LLM (Llama 3.2 model)
# llm = Ollama(model="llama3")

# Initialize Pinecone
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENVIRONMENT = "us-east-1"  # Example: "us-west1-gcp-free"

# Create an instance of the Pinecone class
pc = Pinecone(api_key=PINECONE_API_KEY)

# Load the embedding model
embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# Create or connect to a Pinecone index
index_name = "document-embeddings"
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=384,  # Dimension of the embeddings
        metric="cosine",
        spec=ServerlessSpec(
            cloud="aws",  # Use "gcp" or "azure" if needed
            region="us-east-1"  # Use your preferred region
        )
    )

# Connect to the Pinecone index
index = pc.Index(index_name)

# Home view
def home(request):
    return render(request, "home.html")

# Function to extract text from a PDF file
def load_pdf(pdf_file):
    reader = PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text

# Function to split text into chunks
def split_text_into_chunks(text):
    text_splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_text(text)
    return chunks

# Function to split text into Document objects with metadata
def split_text_into_documents(text, source):
    text_splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_text(text)
    
    # Convert chunks into Document objects with metadata
    documents = [Document(page_content=chunk, metadata={"source": source}) for chunk in chunks]
    return documents

# Function to embed text chunks
def embed_text_chunks(documents):
    # Use the correct method 'embed_documents' to generate embeddings
    embeddings = embedding_model.embed_documents([doc.page_content for doc in documents])
    return embeddings


def process_large_json(file):
    text = ""
    parser = ijson.items(file, "item")  # Adjust the JSON path based on your structure
    for item in parser:
        text += json.dumps(item) + "\n"
        if len(text) > 10000:  # Process in chunks of 10,000 characters
            yield text
            text = ""  # Reset text buffer
    if text:
        yield text

def upload_file(request):
    if request.method == "POST" and request.FILES.get("uploaded_file"):
        uploaded_file = request.FILES["uploaded_file"]
        file_name = uploaded_file.name.lower()

        # Initialize variables
        documents = []

        try:
            # Extract text based on file type
            if file_name.endswith(".pdf"):
                # Process PDF file
                text = load_pdf(uploaded_file)
                # Split text into chunks and convert to Document objects
                documents = split_text_into_documents(text, source=file_name)

            elif file_name.endswith(".json"):
                # Process JSON file in chunks using process_large_json
                for chunk in process_large_json(uploaded_file):
                    documents.extend(split_text_into_documents(chunk, source=file_name))

            else:
                messages.error(request, "Unsupported file format")
                return render(request, "home.html")

            # Validate metadata size before uploading
            for doc in documents:
                metadata_size = sys.getsizeof(doc.metadata)
                if metadata_size > 40960:  # 40 KB limit
                    messages.error(request, f"Metadata size exceeds limit: {metadata_size} bytes")
                    return render(request, "home.html")

            # Generate embeddings and store in Pinecone using LangChain's PineconeVectorStore
            try:
                vectorstore = PineconeVectorStore.from_documents(
                    documents=documents,
                    embedding=embedding_model,
                    index_name=index_name
                )
                messages.success(request, "File uploaded successfully!")
            except Exception as e:
                messages.error(request, f"Failed to upload to Pinecone: {str(e)}")

        except Exception as e:
            messages.error(request, f"An error occurred: {str(e)}")

        return render(request, "home.html")

    return render(request, "home.html")

# User registration view
def register(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  # Log the user in after registration
            return redirect("home")  # Redirect to the home page
    else:
        form = UserCreationForm()
    return render(request, "registration.html", {"form": form})

# User login view
def user_login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("home")  # Redirect to the home page
        else:
            # Return an error message
            return render(request, 'login.html', {'error': 'Invalid username or password'})
    return render(request, 'login.html')

# User logout view
def user_logout(request):
    logout(request)
    return redirect("login")  # Redirect to the login page

from django.core.mail import send_mail
from django.http import HttpResponse
import os

def test_email(request):
    try:
        send_mail(
            'Test Email',
            'This is a test email.',
            os.getenv("EMAIL_HOST_USER"),  # FROM address
            ['melritacyriac123@gmail.com'],  # Replace with a valid recipient email address
            fail_silently=False,
        )
        return HttpResponse("Test email sent.")
    except Exception as e:
        return HttpResponse(f"Error: {e}")

#query documents
def query_documents(request):
    if request.method == "POST":
        query_text = request.POST.get("query")
        if not query_text:
            return JsonResponse({"error": "Query text is required"}, status=400)

        # Convert query to an embedding
        query_embedding = embedding_model.embed_query(query_text)  # Shape: (384,)

        # Search in Pinecone
        search_results = index.query(vector=query_embedding, top_k=5, include_metadata=True)

        # Extract matching documents
        matches = []
        if "matches" in search_results:
            matches = [
                {
                    "score": match["score"],
                    "text": match["metadata"].get("source", "No source available")  # Avoid KeyError
                }
                for match in search_results["matches"]
            ]

        return render(request, "query_documents.html", {"results": matches})

    return render(request, "query_documents.html")


# def query_pinecone(request):
#     if request.method == "POST":
#         query_text = request.POST.get("query")
        
#         if not query_text:
#             return JsonResponse({"error": "Query text is required"}, status=400)

#         # Convert query to an embedding
#         query_embedding = embedding_model.embed_query(query_text)  # Shape: (384,)

#         # Search in Pinecone
#         search_results = index.query(vector=query_embedding, top_k=5, include_metadata=True)

#         # Extract matching documents
#         matches = []
#         if "matches" in search_results:
#             matches = [
#                 {
#                     "score": match["score"],
#                     "text": match["metadata"].get("source", "No source available")  # Avoid KeyError
#                 }
#                 for match in search_results["matches"]
#             ]

#         return render(request, "query.html", {"results": matches})

#     return render(request, "query.html")