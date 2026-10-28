import asyncio

import httpx

from config import OPENALEX_API_KEY


BASE_URL = "https://api.openalex.org/works"


def rebuild_abstract(inv: dict | None) -> str:
    """Susun ulang abstract dari abstract_inverted_index OpenAlex."""
    if not inv:
        return ""

    words = {}

    for word, positions in inv.items():
        for pos in positions:
            words[pos] = word

    return " ".join(words[i] for i in sorted(words))


async def search_papers(
    query: str,
    limit: int = 5,
) -> list[dict]:
    params = {
        "search": query,
        "per_page": max(1, min(limit, 10)),
        "select": (
            "id,doi,title,publication_year,"
            "authorships,abstract_inverted_index,"
            "cited_by_count,type"
        ),
    }

    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Maksimal 2 percobaan untuk error sementara (429 / 5xx).
        for attempt in range(2):
            response = await client.get(BASE_URL, params=params)

            retryable = (
                response.status_code == 429
                or response.status_code >= 500
            )

            if retryable and attempt == 0:
                await asyncio.sleep(1.5)
                continue

            response.raise_for_status()
            return response.json().get("results", [])

    return []