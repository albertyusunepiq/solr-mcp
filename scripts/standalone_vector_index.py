#!/usr/bin/env python3
"""
Standalone script for indexing documents with real vector embeddings from Ollama.
This script has minimal dependencies and directly calls the Ollama API.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import aiohttp
from typing import Dict, List, Any


async def get_embedding_from_ollama(text: str, session: aiohttp.ClientSession) -> List[float]:
    """Get embedding for text from Ollama API.
    
    Args:
        text: Text to get embedding for
        session: aiohttp session
        
    Returns:
        List of floats representing the embedding
    """
    url = "http://localhost:11434/api/embeddings"
    data = {
        "model": "nomic-embed-text",
        "prompt": text
    }
    
    async with session.post(url, json=data) as response:
        if response.status == 200:
            result = await response.json()
            return result["embedding"]
        else:
            error_text = await response.text()
            raise Exception(f"Ollama API error {response.status}: {error_text}")


async def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts using Ollama.
    
    Args:
        texts: List of text strings to generate embeddings for
        
    Returns:
        List of embedding vectors
    """
    embeddings = []
    
    print(f"Generating real embeddings for {len(texts)} documents...")
    
    async with aiohttp.ClientSession() as session:
        for i, text in enumerate(texts):
            try:
                embedding = await get_embedding_from_ollama(text, session)
                embeddings.append(embedding)
                print(f"Generated embedding {i+1}/{len(texts)}")
            except Exception as e:
                print(f"Error generating embedding for document {i+1}: {e}")
                # Use a zero vector as fallback
                embeddings.append([0.0] * 768)
    
    return embeddings


def prepare_field_names(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare field names for Solr using dynamic field naming convention.
    
    Args:
        doc: Original document
        
    Returns:
        Document with properly named fields for Solr
    """
    solr_doc = {}
    
    # Map basic fields (keep as is)
    for field in ['id', 'title', 'content', 'source', 'embedding']:
        if field in doc:
            solr_doc[field] = doc[field]
    
    # Special handling for content if it doesn't exist but text does
    if 'content' not in solr_doc and 'text' in doc:
        solr_doc['content'] = doc['text']
    
    # Map integer fields
    for field in ['section_number', 'dimensions']:
        if field in doc:
            solr_doc[f"{field}_i"] = doc[field]
    
    # Map string fields
    for field in ['author', 'vector_model']:
        if field in doc:
            solr_doc[f"{field}_s"] = doc[field]
    
    # Map date fields
    for field in ['date', 'date_indexed']:
        if field in doc:
            # Format date for Solr
            date_value = doc[field]
            if isinstance(date_value, str):
                if '.' in date_value:  # Has microseconds
                    parts = date_value.split('.')
                    date_value = parts[0] + 'Z'
                elif not date_value.endswith('Z'):
                    date_value = date_value + 'Z'
            solr_doc[f"{field}_dt"] = date_value
    
    # Map multi-valued fields
    for field in ['category', 'tags']:
        if field in doc:
            solr_doc[f"{field}_ss"] = doc[field]
    
    return solr_doc


async def index_documents_with_real_vectors(json_file: str, collection: str = "unified", commit: bool = True):
    """
    Index documents with real vector embeddings into Solr.
    
    Args:
        json_file: Path to the JSON file containing documents
        collection: Solr collection name
        commit: Whether to commit after indexing
    """
    # Load documents
    with open(json_file, 'r', encoding='utf-8') as f:
        documents = json.load(f)
    
    # Extract text for embedding generation
    texts = []
    for doc in documents:
        # Use the 'text' field if it exists, otherwise use 'content'
        if 'text' in doc:
            texts.append(doc['text'])
        elif 'content' in doc:
            texts.append(doc['content'])
        else:
            texts.append(doc.get('title', ''))  # Fallback to title if no text/content
    
    # Generate real embeddings from Ollama
    embeddings = await generate_embeddings(texts)
    
    # Prepare documents for indexing
    solr_docs = []
    for i, doc in enumerate(documents):
        doc_copy = doc.copy()
        
        # Add real vector and metadata
        doc_copy['embedding'] = embeddings[i]
        doc_copy['vector_model'] = 'nomic-embed-text'
        doc_copy['dimensions'] = len(embeddings[i])
        
        # Add current time as date_indexed if not present
        if 'date_indexed' not in doc_copy:
            doc_copy['date_indexed'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        
        # Prepare field names according to Solr conventions
        solr_doc = prepare_field_names(doc_copy)
        solr_docs.append(solr_doc)
    
    # Index documents to Solr
    print(f"Indexing {len(solr_docs)} documents to collection '{collection}'...")
    
    async with aiohttp.ClientSession() as session:
        for i, doc in enumerate(solr_docs):
            solr_url = f"http://localhost:8983/solr/{collection}/update/json/docs"
            params = {"commit": "true"} if (commit and i == len(solr_docs) - 1) else {}
            
            try:
                async with session.post(solr_url, json=doc, params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"Error indexing document {doc['id']}: {response.status} - {error_text}")
                        return False
                        
                    print(f"Indexed document {i+1}/{len(solr_docs)}: {doc['id']}")
                    
            except Exception as e:
                print(f"Error indexing document {doc['id']}: {e}")
                return False
    
    print(f"Successfully indexed {len(solr_docs)} documents with real embeddings to collection '{collection}'")
    return True


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Index documents with real vector embeddings from Ollama")
    parser.add_argument("json_file", help="Path to the JSON file containing documents")
    parser.add_argument("--collection", "-c", default="unified", help="Solr collection name")
    parser.add_argument("--no-commit", dest="commit", action="store_false", help="Don't commit after indexing")
    
    args = parser.parse_args()
    
    if not os.path.isfile(args.json_file):
        print(f"Error: File {args.json_file} not found")
        sys.exit(1)
    
    result = await index_documents_with_real_vectors(args.json_file, args.collection, args.commit)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    asyncio.run(main())
