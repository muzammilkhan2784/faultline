"""Symbol search and detail."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from neo4j.exceptions import ClientError

from faultline.api.deps import ClientDep, fetch_symbol, load_repo
from faultline.api.schemas import SymbolDetail, SymbolHit, SymbolKind
from faultline.graph import cypher, lucene

router = APIRouter(prefix="/repos/{repo_id}/symbols", tags=["symbols"])


def lucene_query(text: str) -> str:
    """Turn what a person typed into a safe, prefix-friendly fulltext query.

    Dotted names are split on ``.`` and ``_`` and every term becomes a prefix
    match, so ``verify tok`` finds ``auth.verify_token`` as you type.
    """
    terms = [t for t in re.split(r"[\s._]+", text.strip()) if t]
    return " AND ".join(lucene.escape(term) + "*" for term in terms)


@router.get("", response_model=list[SymbolHit])
def search_symbols(
    repo_id: str,
    client: ClientDep,
    q: str = Query(min_length=1, max_length=200),
    kind: SymbolKind | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    load_repo(client, repo_id)
    kinds = [kind] if kind else None
    query = lucene_query(q)
    if query:
        try:
            rows = client.read(
                cypher.SEARCH_SYMBOLS_FULLTEXT,
                repo_id=repo_id,
                search=query,
                kinds=kinds,
                limit=limit,
            )
            if rows:
                return rows
        except ClientError:
            # No fulltext index on this server: fall through to a plain scan.
            pass
    return client.read(
        cypher.SEARCH_SYMBOLS_SCAN,
        repo_id=repo_id,
        needle=q.strip().lower(),
        kinds=kinds,
        limit=limit,
    )


@router.get("/{fqn:path}", response_model=SymbolDetail)
def get_symbol(repo_id: str, fqn: str, client: ClientDep) -> dict[str, Any]:
    load_repo(client, repo_id)
    symbol = fetch_symbol(client, repo_id, fqn)
    if symbol is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    return symbol
