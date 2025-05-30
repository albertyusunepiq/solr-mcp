"""SolrCloud client implementation."""

import logging
from typing import Any, Dict, List, Optional, Tuple

import pysolr
from loguru import logger

from solr_mcp.solr.collections import (
    HttpCollectionProvider,
    ZooKeeperCollectionProvider,
)
from solr_mcp.solr.config import SolrConfig
from solr_mcp.solr.exceptions import (
    ConnectionError,
    DocValuesError,
    QueryError,
    SolrError,
    SQLExecutionError,
    SQLParseError,
)
from solr_mcp.solr.interfaces import CollectionProvider, VectorSearchProvider
from solr_mcp.solr.query import QueryBuilder
from solr_mcp.solr.query.executor import QueryExecutor
from solr_mcp.solr.response import ResponseFormatter
from solr_mcp.solr.schema import FieldManager
from solr_mcp.solr.vector import VectorManager, VectorSearchResults
from solr_mcp.vector_provider import OllamaVectorProvider
from solr_mcp.vector_provider.constants import MODEL_DIMENSIONS

logger = logging.getLogger(__name__)


class SolrClient:
    """Client for interacting with SolrCloud."""

    def __init__(
        self,
        config: SolrConfig,
        collection_provider: Optional[CollectionProvider] = None,
        solr_client: Optional[pysolr.Solr] = None,
        field_manager: Optional[FieldManager] = None,
        vector_provider: Optional[VectorSearchProvider] = None,
        query_builder: Optional[QueryBuilder] = None,
        query_executor: Optional[QueryExecutor] = None,
        response_formatter: Optional[ResponseFormatter] = None,
    ):
        """Initialize the SolrClient with the given configuration and optional dependencies.

        Args:
            config: Configuration for the client
            collection_provider: Optional collection provider implementation
            solr_client: Optional pre-configured Solr client
            field_manager: Optional pre-configured field manager
            vector_provider: Optional vector search provider implementation
            query_builder: Optional pre-configured query builder
            query_executor: Optional pre-configured query executor
            response_formatter: Optional pre-configured response formatter
        """
        self.config = config
        self.base_url = config.solr_base_url.rstrip("/")

        # Initialize collection provider
        if collection_provider:
            self.collection_provider = collection_provider
        elif self.config.zookeeper_hosts:
            # Use ZooKeeper if hosts are specified
            self.collection_provider = ZooKeeperCollectionProvider(
                hosts=self.config.zookeeper_hosts
            )
        else:
            # Otherwise use HTTP provider
            self.collection_provider = HttpCollectionProvider(base_url=self.base_url)

        # Initialize field manager
        self.field_manager = field_manager or FieldManager(self.base_url)

        # Initialize vector provider
        self.vector_provider = vector_provider or OllamaVectorProvider()

        # Initialize query builder
        self.query_builder = query_builder or QueryBuilder(
            field_manager=self.field_manager
        )

        # Initialize query executor
        self.query_executor = query_executor or QueryExecutor(base_url=self.base_url)

        # Initialize response formatter
        self.response_formatter = response_formatter or ResponseFormatter()

        # Initialize vector manager with default top_k of 10
        self.vector_manager = VectorManager(
            self, self.vector_provider, 10  # Default value for top_k
        )

        # Initialize Solr client
        self._solr_client = solr_client
        self._default_collection = None

    async def _get_or_create_client(self, collection: str) -> pysolr.Solr:
        """Get or create a Solr client for the given collection.

        Args:
            collection: Collection name to use.

        Returns:
            Configured Solr client

        Raises:
            SolrError: If no collection is specified
        """
        if not collection:
            raise SolrError("No collection specified")

        if not self._solr_client:
            self._solr_client = pysolr.Solr(
                f"{self.base_url}/{collection}", timeout=self.config.connection_timeout
            )

        return self._solr_client

    async def list_collections(self) -> List[str]:
        """List all available collections."""
        try:
            return await self.collection_provider.list_collections()
        except Exception as e:
            raise SolrError(f"Failed to list collections: {str(e)}")

    async def list_fields(self, collection: str) -> List[Dict[str, Any]]:
        """List all fields in a collection with their properties."""
        try:
            return await self.field_manager.list_fields(collection)
        except Exception as e:
            raise SolrError(
                f"Failed to list fields for collection '{collection}': {str(e)}"
            )

    def _format_search_results(
        self, results: pysolr.Results, start: int = 0
    ) -> Dict[str, Any]:
        """Format Solr search results for LLM consumption."""
        return self.response_formatter.format_search_results(results, start)

    async def execute_select_query(self, query: str) -> Dict[str, Any]:
        """Execute a SQL SELECT query against Solr using the SQL interface."""
        try:
            # Parse and validate query
            logger.debug(f"Original query: {query}")
            preprocessed_query = self.query_builder.parser.preprocess_query(query)
            logger.debug(f"Preprocessed query: {preprocessed_query}")
            ast, collection, _ = self.query_builder.parse_and_validate_select(
                preprocessed_query
            )
            logger.debug(f"Parsed collection: {collection}")

            # Delegate execution to the query executor
            return await self.query_executor.execute_select_query(
                query=preprocessed_query, collection=collection
            )

        except (DocValuesError, SQLParseError, SQLExecutionError):
            # Re-raise these specific exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise SQLExecutionError(f"SQL query failed: {str(e)}")

    async def execute_vector_select_query(
        self, query: str, vector: List[float], field: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute SQL query filtered by vector similarity search.

        Args:
            query: SQL query to execute
            vector: Query vector for similarity search
            field: Optional name of the vector field to search against. If not provided, the first vector field will be auto-detected.

        Returns:
            Query results

        Raises:
            SolrError: If search fails
            QueryError: If query execution fails
        """
        try:
            # Parse and validate query
            ast, collection, _ = self.query_builder.parse_and_validate_select(query)

            # Validate and potentially auto-detect vector field
            field, field_info = await self.vector_manager.validate_vector_field(
                collection=collection, field=field
            )

            # Get limit and offset from query
            limit = 10  # Default limit
            if ast.args.get("limit"):
                try:
                    limit_expr = ast.args["limit"]
                    if hasattr(limit_expr, "expression"):
                        # Handle case where expression is a Literal
                        if hasattr(limit_expr.expression, "this"):
                            limit = int(limit_expr.expression.this)
                        else:
                            limit = int(limit_expr.expression)
                    else:
                        limit = int(limit_expr)
                except (ValueError, AttributeError):
                    limit = 10  # Fallback to default

            offset = ast.args.get("offset", 0)

            # For KNN search, we need to fetch limit + offset results to account for pagination
            top_k = limit + offset

            # Execute vector search
            client = await self._get_or_create_client(collection)
            results = await self.vector_manager.execute_vector_search(
                client=client, vector=vector, field=field, top_k=top_k
            )

            # Convert to VectorSearchResults
            vector_results = VectorSearchResults.from_solr_response(
                response=results, top_k=top_k
            )

            # Build SQL query with vector results
            doc_ids = vector_results.get_doc_ids()

            # Execute SQL query with the vector results
            stmt = query  # Start with original query

            # Check if query already has WHERE clause
            has_where = "WHERE" in stmt.upper()
            has_limit = "LIMIT" in stmt.upper()

            # Extract limit part if present to reposition it
            limit_part = ""
            if has_limit:
                # Use case-insensitive find and split
                limit_index = stmt.upper().find("LIMIT")
                stmt_before_limit = stmt[:limit_index].strip()
                limit_part = stmt[limit_index + 5 :].strip()  # +5 to skip "LIMIT"
                stmt = stmt_before_limit  # This is everything before LIMIT

            # Add WHERE clause at the proper position
            if doc_ids:
                # Add filter query if present
                if has_where:
                    stmt = f"{stmt} AND id IN ({','.join(doc_ids)})"
                else:
                    stmt = f"{stmt} WHERE id IN ({','.join(doc_ids)})"
            else:
                # No vector search results, return empty result set
                if has_where:
                    stmt = f"{stmt} AND 1=0"  # Always false condition
                else:
                    stmt = f"{stmt} WHERE 1=0"  # Always false condition

            # Add limit back at the end if it was present or add default limit
            if limit_part:
                stmt = f"{stmt} LIMIT {limit_part}"
            elif not has_limit:
                stmt = f"{stmt} LIMIT {limit}"

            # Execute the SQL query
            return await self.query_executor.execute_select_query(
                query=stmt, collection=collection
            )

        except Exception as e:
            if isinstance(e, (QueryError, SolrError)):
                raise
            raise QueryError(f"Error executing vector query: {str(e)}")

    async def execute_semantic_select_query(
        self,
        query: str,
        text: str,
        field: Optional[str] = None,
        vector_provider_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run a SQL-SELECT filtered by semantic similarity using direct Ollama + Solr approach.
        This replaces the complex two-step process with the proven standalone script logic.
        """
        try:
            import aiohttp
            import json
            
            # ── 1. Parse SQL to get collection and limits ──────────────────────────
            ast, collection, _ = self.query_builder.parse_and_validate_select(query)
            
            limit = 10
            offset = 0
            if ast.args.get("limit"):
                try:
                    limit = int(
                        getattr(ast.args["limit"], "expression", ast.args["limit"]).this
                        if hasattr(ast.args["limit"], "expression")
                        else ast.args["limit"]
                    )
                except Exception:
                    limit = 10
            if ast.args.get("offset"):
                offset = int(ast.args["offset"])

            # ── 2. Generate embedding directly via Ollama API ──────────────────────
            async def get_embedding_from_ollama(text: str) -> List[float]:
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

            # ── 3. Execute vector search directly via Solr API ─────────────────────
            async def vector_search_solr(query_vector: List[float], collection: str, top_k: int):
                # Use embedding field (already confirmed to exist)
                vector_str = "[" + ",".join(str(x) for x in query_vector) + "]"
                
                solr_url = f"http://localhost:8983/solr/{collection}/select"
                
                # Extract field names from original SQL query for response
                fields_to_return = self._extract_fields_from_sql(query)
                
                query_data = {
                    "query": f"{{!knn f=embedding topK={top_k}}}{vector_str}",
                    "fields": fields_to_return,
                    "limit": top_k,
                    "offset": offset
                }
                
                headers = {"Content-Type": "application/json"}
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(solr_url, json=query_data, headers=headers) as response:
                        if response.status == 200:
                            solr_result = await response.json()
                            
                            # Convert Solr response to MCP format
                            docs = solr_result.get("response", {}).get("docs", [])
                            num_found = solr_result.get("response", {}).get("numFound", 0)
                            
                            # Add EOF marker like other MCP responses
                            if docs:
                                docs.append({"EOF": True})
                            
                            return {
                                "result-set": {
                                    "docs": docs,
                                    "numFound": num_found + 1,  # +1 for EOF
                                    "start": offset
                                }
                            }
                        else:
                            error_text = await response.text()
                            raise Exception(f"Solr error {response.status}: {error_text}")

            # ── 4. Execute the direct approach ─────────────────────────────────────
            query_vector = await get_embedding_from_ollama(text)
            result = await vector_search_solr(query_vector, collection, limit)
            
            return result

        except Exception as exc:
            raise SolrError(f"Semantic search failed: {exc}") from exc


    def _extract_fields_from_sql(self, query: str) -> str:
        """Extract field list from SQL query for Solr field parameter."""
        query_upper = query.upper().strip()
        
        if query_upper.startswith("SELECT *"):
            return "*"
        elif query_upper.startswith("SELECT "):
            # Extract field list between SELECT and FROM
            select_part = query[6:].strip()  # Remove "SELECT "
            from_index = select_part.upper().find(" FROM ")
            if from_index != -1:
                fields_part = select_part[:from_index].strip()
                return fields_part
        
        return "id,title,content,score"  # default fields
