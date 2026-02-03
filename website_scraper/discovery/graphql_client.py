"""
GraphQL API client for AdmitONE events
"""

import json
from typing import Any, Dict, Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config import ScraperConfig, DEFAULT_CONFIG

logger = structlog.get_logger()


class GraphQLClient:
    """Async GraphQL client for graphql.admitone.com"""

    def __init__(self, config: ScraperConfig = None):
        self.config = config or DEFAULT_CONFIG

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def query(
        self,
        operation_name: str,
        query_str: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a GraphQL query.

        Args:
            operation_name: GraphQL operation name
            query_str: GraphQL query string
            variables: Optional query variables

        Returns:
            Parsed JSON response data or None on failure
        """
        payload: Dict[str, Any] = {
            "operationName": operation_name,
            "query": query_str,
        }
        if variables:
            payload["variables"] = variables

        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.config.user_agent,
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.request_timeout) as client:
                response = await client.post(
                    self.config.graphql_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

                data = response.json()

                if "errors" in data:
                    for err in data["errors"]:
                        logger.warning("graphql_error", message=err.get("message"), operation=operation_name)
                    return None

                return data.get("data")

        except httpx.HTTPStatusError as e:
            logger.error("graphql_http_error", status=e.response.status_code, operation=operation_name)
            raise
        except json.JSONDecodeError as e:
            logger.error("graphql_json_error", error=str(e), operation=operation_name)
            return None
        except Exception as e:
            logger.error("graphql_request_failed", error=str(e), operation=operation_name)
            raise
