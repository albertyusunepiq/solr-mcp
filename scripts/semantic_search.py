#!/usr/bin/env python3
"""
Standalone script for semantic search using Ollama embeddings and Solr.
This bypasses the MCP server and directly queries Solr with vector similarity.
"""

import argparse
import asyncio
import json
import sys
import aiohttp
from typing import List, Dict, Any


async def get_embedding_from_ollama(text: str) -> List[float]:
    """Get embedding for text from Ollama API."""
    url = "http://localhost:11434/api/embeddings"
    data = {
        "model": "nomic-embed-text",
        "prompt": text
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result["embedding"]
            else:
                error_text = await response.text()
                raise Exception(f"Ollama API error {response.status}: {error_text}")


async def vector_search_solr(query_vector: List[float], collection: str = "unified", top_k: int = 5) -> Dict[str, Any]:
    """Perform vector similarity search in Solr using POST request with KNN query."""
    
    # Format vector for Solr KNN query
    vector_str = "[" + ",".join(str(x) for x in query_vector) + "]"
    
    # Build Solr query with KNN using POST
    solr_url = f"http://localhost:8983/solr/{collection}/select"
    
    # Use JSON query format for POST
    query_data = {
        "query": f"{{!knn f=embedding topK={top_k}}}{vector_str}",
        "fields": "id,title,content,score",
        "limit": top_k
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(solr_url, json=query_data, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                error_text = await response.text()
                raise Exception(f"Solr error {response.status}: {error_text}")


async def hybrid_search_solr(query_vector: List[float], text_query: str = None, collection: str = "unified", top_k: int = 5) -> Dict[str, Any]:
    """Perform hybrid search combining vector similarity and text search."""
    
    solr_url = f"http://localhost:8983/solr/{collection}/select"
    
    # Build query combining vector and text search
    vector_str = "[" + ",".join(str(x) for x in query_vector) + "]"
    knn_query = f"{{!knn f=embedding topK={top_k * 2}}}{vector_str}"
    
    if text_query:
        # Combine vector search with text search
        full_query = f"({knn_query}) OR (content:{text_query} OR title:{text_query})"
    else:
        full_query = knn_query
    
    query_data = {
        "query": full_query,
        "fields": "id,title,content,score",
        "limit": top_k
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(solr_url, json=query_data, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                error_text = await response.text()
                raise Exception(f"Solr error {response.status}: {error_text}")


async def semantic_search(query_text: str, collection: str = "unified", top_k: int = 5, hybrid: bool = False):
    """Perform end-to-end semantic search."""
    
    print(f"Searching for: '{query_text}'")
    print(f"Collection: {collection}")
    print(f"Top results: {top_k}")
    print(f"Search type: {'Hybrid' if hybrid else 'Vector only'}")
    print("-" * 50)
    
    try:
        # Step 1: Get embedding for query text
        print("Generating query embedding...")
        query_vector = await get_embedding_from_ollama(query_text)
        print(f"Query vector dimension: {len(query_vector)}")
        
        # Step 2: Search Solr with vector
        print("Searching Solr...")
        if hybrid:
            results = await hybrid_search_solr(query_vector, query_text, collection, top_k)
        else:
            results = await vector_search_solr(query_vector, collection, top_k)
        
        # Step 3: Display results
        response = results.get("response", {})
        docs = response.get("docs", [])
        num_found = response.get("numFound", 0)
        
        print(f"Found {num_found} results:")
        print("=" * 50)
        
        for i, doc in enumerate(docs, 1):
            score = doc.get("score", 0)
            title = doc.get("title", ["Unknown"])[0] if isinstance(doc.get("title"), list) else doc.get("title", "Unknown")
            content = doc.get("content", [""])[0] if isinstance(doc.get("content"), list) else doc.get("content", "")
            
            print(f"{i}. {title}")
            print(f"   Score: {score:.4f}")
            print(f"   ID: {doc.get('id', 'Unknown')}")
            print(f"   Content: {content[:200]}...")
            print("-" * 50)
        
        return docs
        
    except Exception as e:
        print(f"Error during semantic search: {e}")
        return []


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Semantic search using Ollama embeddings and Solr")
    parser.add_argument("query", help="Search query text")
    parser.add_argument("--collection", "-c", default="unified", help="Solr collection name")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="Number of results to return")
    parser.add_argument("--hybrid", action="store_true", help="Use hybrid search (vector + text)")
    
    args = parser.parse_args()
    
    results = await semantic_search(args.query, args.collection, args.top_k, args.hybrid)
    
    if not results:
        print("No results found or search failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
