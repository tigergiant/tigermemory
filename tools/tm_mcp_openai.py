#!/usr/bin/env python3
"""
OpenAI / ChatGPT-facing MCP facade for tigermemory.

This server intentionally exposes a narrow ChatGPT-facing surface:

- search(query) -> OpenAI company-knowledge compatible result list
- fetch(id) -> OpenAI company-knowledge compatible full document fetch
- get_agent_onboarding(depth) -> read-only tigermemory operating context
- write_memory(topic, text) -> LLM-routed memory write as agent "chatgpt"

It does not register the full tigermemory wiki/source/admin/media/expense toolset.
The existing tools/tm_mcp.py endpoint remains unchanged for current MCP clients.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Literal

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import ToolAnnotations
from pydantic import AnyUrl, BaseModel, Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import tm_core
import tm_memory_ops
import tm_persona


READ_SCOPE = "tm:read"
WRITE_MEMORY_SCOPE = "tm:write_memory"
WRITE_AGENT = "chatgpt"
DEFAULT_PUBLIC_BASE = "https://tm-openai.doodiu.cloud"
DEFAULT_STORE_PATH = tm_core.REPO_ROOT / "runtime" / "openmemory" / "openai-mcp-oauth.json"
DEFAULT_MAX_FETCH_CHARS = 80_000
MAX_WRITE_MEMORY_CHARS = 4_000
EXTRA_READONLY_DOCS = ("AGENTS.md",)
READYZ_TIMEOUT_SECONDS = float(os.environ.get("TM_OPENAI_MCP_READYZ_TIMEOUT", "8.0"))

_DEFAULT_ALLOWED_HOSTS = [
    "localhost", "localhost:*",
    "127.0.0.1", "127.0.0.1:*",
    "tm-openai.doodiu.cloud", "tm-openai.doodiu.cloud:*",
]
_allowed_hosts_env = os.environ.get("TM_OPENAI_MCP_ALLOWED_HOSTS", "").strip()
_allowed_hosts = (
    [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
    if _allowed_hosts_env
    else _DEFAULT_ALLOWED_HOSTS
)


class SearchResult(BaseModel):
    id: str
    title: str
    url: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: list[SearchResult]


class FetchResponse(BaseModel):
    id: str
    title: str
    text: str
    url: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OnboardingResponse(BaseModel):
    depth: str
    content: str
    sources: list[str]


class WriteMemoryResponse(BaseModel):
    route: str
    score: int | None = None
    topic_inferred: str | None = None
    id: str | None = None
    path: str | None = None
    commit_sha: str | None = None
    reasons: str | None = None
    verified: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class _JsonStore:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"clients": {}, "pending": {}, "codes": {}, "access_tokens": {}, "refresh_tokens": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class TigermemoryOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Small single-user OAuth provider for ChatGPT connector linking.

    The human approval step is protected by TM_OPENAI_MCP_LINK_SECRET. This is
    deliberately scoped to connector dogfooding; production multi-user auth
    should move to an established IdP as OpenAI recommends.
    """

    def __init__(self, issuer_url: str, link_secret: str, store_path: pathlib.Path) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.link_secret = link_secret
        self.store = _JsonStore(store_path)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.store.load()
        raw = data["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id required")
        data = self.store.load()
        data["clients"][client_info.client_id] = client_info.model_dump(mode="json", exclude_none=True)
        self.store.save(data)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(24)
        data = self.store.load()
        data["pending"][request_id] = {
            "client_id": client.client_id,
            "state": params.state,
            "scopes": params.scopes or [READ_SCOPE],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
            "expires_at": time.time() + 600,
        }
        self.store.save(data)
        return f"{self.issuer_url}/oauth/approve?request={urllib.parse.quote(request_id)}"

    def approve_pending(self, request_id: str, submitted_secret: str) -> str:
        if not secrets.compare_digest(submitted_secret, self.link_secret):
            raise PermissionError("invalid link secret")
        data = self.store.load()
        pending = data["pending"].pop(request_id, None)
        if not pending or pending["expires_at"] < time.time():
            self.store.save(data)
            raise ValueError("authorization request expired or not found")
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=pending["scopes"],
            expires_at=time.time() + 300,
            client_id=pending["client_id"],
            code_challenge=pending["code_challenge"],
            redirect_uri=AnyUrl(pending["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(pending["redirect_uri_provided_explicitly"]),
            resource=pending.get("resource"),
        )
        data["codes"][code] = auth_code.model_dump(mode="json")
        self.store.save(data)
        return construct_redirect_uri(pending["redirect_uri"], code=code, state=pending.get("state"))

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        data = self.store.load()
        raw = data["codes"].get(authorization_code)
        if not raw or raw.get("client_id") != client.client_id:
            return None
        return AuthorizationCode.model_validate(raw)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        data = self.store.load()
        data["codes"].pop(authorization_code.code, None)
        token, refresh = self._issue_tokens(
            data,
            client_id=str(client.client_id),
            scopes=authorization_code.scopes or [READ_SCOPE],
            resource=authorization_code.resource,
        )
        self.store.save(data)
        return OAuthToken(
            access_token=token.token,
            expires_in=3600,
            scope=" ".join(token.scopes),
            refresh_token=refresh.token,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        data = self.store.load()
        raw = data["refresh_tokens"].get(refresh_token)
        if not raw or raw.get("client_id") != client.client_id:
            return None
        token = RefreshToken.model_validate(raw)
        if token.expires_at is not None and token.expires_at < int(time.time()):
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        requested = scopes or refresh_token.scopes
        if any(scope not in refresh_token.scopes for scope in requested):
            raise TokenError("invalid_scope", "requested scope was not granted")
        data = self.store.load()
        data["refresh_tokens"].pop(refresh_token.token, None)
        token, new_refresh = self._issue_tokens(
            data,
            client_id=str(client.client_id),
            scopes=requested,
            resource=None,
        )
        self.store.save(data)
        return OAuthToken(
            access_token=token.token,
            expires_in=3600,
            scope=" ".join(token.scopes),
            refresh_token=new_refresh.token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = self.store.load()
        raw = data["access_tokens"].get(token)
        if not raw:
            return None
        access = AccessToken.model_validate(raw)
        if access.expires_at is not None and access.expires_at < int(time.time()):
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        data = self.store.load()
        data["access_tokens"].pop(token.token, None)
        data["refresh_tokens"].pop(token.token, None)
        self.store.save(data)

    def _issue_tokens(
        self,
        data: dict[str, Any],
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> tuple[AccessToken, RefreshToken]:
        now = int(time.time())
        access = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 3600,
            resource=resource,
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 2_592_000,
        )
        data["access_tokens"][access.token] = access.model_dump(mode="json")
        data["refresh_tokens"][refresh.token] = refresh.model_dump(mode="json")
        return access, refresh


def _tool_meta(scopes: list[str] | None = None) -> dict[str, Any]:
    return {"securitySchemes": [{"type": "oauth2", "scopes": scopes or [READ_SCOPE]}]}


READ_ONLY_TOOL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE_MEMORY_TOOL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def _probe_url(
    name: str,
    url: str,
    *,
    timeout: float = READYZ_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "tigermemory-openai-readyz/1.0",
            **(headers or {}),
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp.read(1024)
            status = getattr(resp, "status", 200)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "ok": False,
            "name": name,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": 200 <= int(status) < 300,
        "name": name,
        "status": int(status),
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def _readyz_payload() -> tuple[int, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {
        "repo": {
            "ok": tm_core.REPO_ROOT.exists(),
        },
        "oauth_store": {
            "ok": _oauth_store_path().parent.exists() and os.access(_oauth_store_path().parent, os.W_OK),
        },
    }

    mem0_url = f"{tm_core.mem0_base().rstrip('/')}/api/v1/memories/?user_id=tiger&page=1&size=1"
    checks["mem0"] = _probe_url("mem0", mem0_url)

    embedding_base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
    if embedding_base:
        headers = {}
        api_key = os.environ.get("EMBEDDING_API_KEY", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        checks["embedding"] = _probe_url("embedding", f"{embedding_base}/models", headers=headers)
    else:
        checks["embedding"] = {
            "ok": True,
            "skipped": True,
            "reason": "EMBEDDING_BASE_URL is not configured; search will degrade to lexical where possible",
        }

    ok = all(bool(check.get("ok")) for check in checks.values())
    return (200 if ok else 503), {
        "ok": ok,
        "server": "tigermemory-openai",
        "checks": checks,
    }


def _build_mcp(auth_mode: str, public_base: str, link_secret: str | None, store_path: pathlib.Path) -> FastMCP:
    kwargs: dict[str, Any] = {
        "name": "tigermemory-openai",
        "instructions": (
            "Narrow connector for tigermemory. Use search first, then fetch by id. "
            "For durable notes, use write_memory; the tigermemory service routes it "
            "to mem0, inbox, or discard. This facade intentionally does not expose "
            "wiki/source/admin/media/expense tools."
        ),
        "transport_security": TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts,
        ),
    }
    provider: TigermemoryOAuthProvider | None = None
    if auth_mode == "oauth":
        if not link_secret:
            raise RuntimeError("TM_OPENAI_MCP_LINK_SECRET is required for --auth oauth")
        provider = TigermemoryOAuthProvider(public_base, link_secret, store_path)
        kwargs["auth_server_provider"] = provider
        kwargs["auth"] = AuthSettings(
            issuer_url=public_base,
            service_documentation_url=f"{public_base}/healthz",
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[READ_SCOPE, WRITE_MEMORY_SCOPE],
                default_scopes=[READ_SCOPE, WRITE_MEMORY_SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[READ_SCOPE, WRITE_MEMORY_SCOPE],
            resource_server_url=public_base,
        )

    server = FastMCP(**kwargs)

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"ok": True, "server": "tigermemory-openai", "auth": auth_mode})

    @server.custom_route("/readyz", methods=["GET"], include_in_schema=False)
    async def readyz(_request: Request) -> Response:
        status, payload = _readyz_payload()
        return JSONResponse(payload, status_code=status)

    if provider is not None:

        @server.custom_route("/oauth/approve", methods=["GET", "POST"], include_in_schema=False)
        async def oauth_approve(request: Request) -> Response:
            if request.method == "GET":
                request_id = request.query_params.get("request", "")
                return HTMLResponse(
                    "<!doctype html><title>tigermemory ChatGPT link</title>"
                    "<h1>Approve tigermemory ChatGPT connector</h1>"
                    "<form method='post'>"
                    f"<input type='hidden' name='request' value='{request_id}'>"
                    "<label>Link secret <input name='secret' type='password' autofocus></label>"
                    "<button type='submit'>Approve</button>"
                    "</form>",
                    status_code=200,
                )
            form = await request.form()
            try:
                redirect_to = provider.approve_pending(
                    str(form.get("request") or ""),
                    str(form.get("secret") or ""),
                )
            except PermissionError:
                return HTMLResponse("Invalid link secret.", status_code=403)
            except ValueError as exc:
                return HTMLResponse(str(exc), status_code=400)
            return RedirectResponse(redirect_to, status_code=302)

    return server


def _page_id(path: str) -> str:
    encoded = base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")
    return f"page:{encoded}"


def _decode_page_id(item_id: str) -> str:
    raw = item_id.removeprefix("page:")
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")


def _mem0_id(item_id: str) -> str:
    raw = item_id.removeprefix("mem0:")
    uuid.UUID(raw)
    return raw


def _page_url(path: str) -> str:
    return tm_core.git_remote_blob_url(path) or f"tigermemory://{path}"


def _mem0_url(mem_id: str) -> str:
    return f"tigermemory://mem0/{mem_id}"


def _write_memory_via_router(topic: str, text: str) -> WriteMemoryResponse:
    clean_topic = (topic or "").strip()
    clean_text = (text or "").strip()
    tm_core.validate_topic(clean_topic)
    if not clean_text:
        raise ValueError("text must be non-empty")
    if len(clean_text) > MAX_WRITE_MEMORY_CHARS:
        raise ValueError(f"text must be <= {MAX_WRITE_MEMORY_CHARS} characters")
    data = tm_memory_ops.write_memory_with_review(
        WRITE_AGENT,
        clean_topic,
        clean_text,
        force_inbox=False,
        total_budget_s=25,
        include_readback=True,
    )
    return WriteMemoryResponse(
        route=str(data.get("route") or ""),
        score=data.get("score") if isinstance(data.get("score"), int) else None,
        topic_inferred=data.get("topic_inferred") if isinstance(data.get("topic_inferred"), str) else None,
        id=data.get("id") if isinstance(data.get("id"), str) else None,
        path=data.get("path") if isinstance(data.get("path"), str) else None,
        commit_sha=data.get("commit_sha") if isinstance(data.get("commit_sha"), str) else None,
        reasons=data.get("reasons") if isinstance(data.get("reasons"), str) else None,
        verified=data.get("verified") if isinstance(data.get("verified"), dict) else None,
        raw=data,
    )


def _oauth_store_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TM_OPENAI_MCP_OAUTH_STORE", str(DEFAULT_STORE_PATH)))


def _stored_client_has_write_scope(client_id: str) -> bool:
    path = _oauth_store_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    now = int(time.time())
    for section in ("access_tokens", "refresh_tokens"):
        for token in data.get(section, {}).values():
            if token.get("client_id") != client_id:
                continue
            if token.get("expires_at") is not None and token["expires_at"] < now:
                continue
            if WRITE_MEMORY_SCOPE in (token.get("scopes") or []):
                return True
    client = data.get("clients", {}).get(client_id) or {}
    return WRITE_MEMORY_SCOPE in str(client.get("scope") or "").split()


def _require_write_memory_scope(ctx: Context | None = None) -> None:
    access = get_access_token()
    if access is not None:
        if WRITE_MEMORY_SCOPE in access.scopes:
            return
        raise PermissionError(
            f"missing required scope: {WRITE_MEMORY_SCOPE}; reconnect the tigermemory ChatGPT connector "
            "to approve write_memory access"
        )
    if ctx is None:
        return
    client_id = ctx.client_id
    if not client_id or not _stored_client_has_write_scope(client_id):
        raise PermissionError(
            f"missing required scope: {WRITE_MEMORY_SCOPE}; reconnect the tigermemory ChatGPT connector "
            "to approve write_memory access"
        )


def _safe_text_file(path: str) -> pathlib.Path:
    rel = path.replace("\\", "/").strip()
    if rel.startswith("/") or rel.startswith("../") or "/../" in rel:
        raise ValueError("invalid path")
    if not (rel.startswith("wiki/") or rel.startswith("sources/") or rel in EXTRA_READONLY_DOCS):
        raise ValueError("fetch only allows wiki/, sources/, and approved root documents")
    full = (tm_core.REPO_ROOT / rel).resolve()
    full.relative_to(tm_core.REPO_ROOT.resolve())
    if not full.is_file():
        raise FileNotFoundError(rel)
    return full


def _title_from_text(path: str, text: str) -> str:
    if text.startswith("---\n"):
        for line in text.splitlines()[1:40]:
            if line == "---":
                break
            if line.startswith("title:"):
                return line.removeprefix("title:").strip().strip("\"'")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return pathlib.PurePosixPath(path).stem.replace("-", " ")


def _query_terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{2,}", query.casefold())
    terms: list[str] = []
    for term in raw_terms:
        if term not in terms:
            terms.append(term)
    return terms


def _snippet_around(text: str, terms: list[str], *, max_chars: int = 500) -> str:
    lowered = text.casefold()
    start = 0
    for term in terms:
        index = lowered.find(term)
        if index >= 0:
            start = max(0, index - 180)
            break
    snippet = " ".join(text[start:start + max_chars].split())
    return snippet


def _search_extra_doc_results(query: str, limit: int) -> list[SearchResult]:
    terms = _query_terms(query)
    if not terms:
        return []

    results: list[SearchResult] = []
    for path in EXTRA_READONLY_DOCS:
        full = tm_core.REPO_ROOT / path
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        lowered = text.casefold()
        header = "\n".join(text.splitlines()[:80]).casefold()
        score = 0.0
        if query.casefold() in lowered:
            score += 20.0
        for term in terms:
            if term in header:
                score += 5.0
            elif term in lowered:
                score += 1.0
        if score <= 0:
            continue
        results.append(SearchResult(
            id=_page_id(path),
            title=_title_from_text(path, text),
            url=_page_url(path),
            metadata={
                "source": "repo",
                "path": path,
                "score": score,
                "snippet": _snippet_around(text, terms),
            },
        ))
    results.sort(key=lambda item: float(item.metadata.get("score") or 0.0), reverse=True)
    return results[:limit]


def _has_strong_extra_doc_match(results: list[SearchResult]) -> bool:
    if not results:
        return False
    return float(results[0].metadata.get("score") or 0.0) >= 10.0


_EXTRA_DOC_FAST_PATH_TERMS = {
    "agents",
    "agent",
    "agentrules",
    "rebase",
    "conflict",
    "abort",
    "git",
    "hook",
    "preflight",
    "onboarding",
    "dirty",
    "worktree",
    "commit",
    "push",
    "pull",
    "inbox",
    "write_memory",
}


def _should_fast_path_extra_docs(query: str, results: list[SearchResult]) -> bool:
    if not _has_strong_extra_doc_match(results):
        return False
    terms = set(_query_terms(query))
    return bool(terms & _EXTRA_DOC_FAST_PATH_TERMS)


def _search_wiki_results(query: str, limit: int) -> list[SearchResult]:
    hits = tm_core.search_wiki_hybrid(query, size=limit, include_sources=True, include_inbox=False)
    results: list[SearchResult] = []
    for hit in hits:
        path = str(hit.get("path", ""))
        if not path.startswith(("wiki/", "sources/")):
            continue
        results.append(SearchResult(
            id=_page_id(path),
            title=str(hit.get("title") or pathlib.PurePosixPath(path).stem),
            url=_page_url(path),
            metadata={
                "source": "repo",
                "path": path,
                "score": hit.get("score"),
                "snippet": hit.get("snippet"),
            },
        ))
    return results


def _search_mem0_results(query: str, limit: int) -> list[SearchResult]:
    try:
        data = json.loads(tm_core.mem0_search(query, size=limit))
    except Exception:
        return []
    items = data.get("items") or data.get("results") or []
    results: list[SearchResult] = []
    for index, item in enumerate(items[:limit], 1):
        mem_id = str(item.get("id") or "")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", mem_id):
            continue
        metadata = item.get("metadata_") or item.get("metadata") or {}
        text = str(item.get("content") or item.get("memory") or item.get("text") or "")
        topic = metadata.get("topic", "memory")
        source = metadata.get("source", "unknown")
        results.append(SearchResult(
            id=f"mem0:{mem_id}",
            title=f"Memory {index}: {topic} / {source}",
            url=_mem0_url(mem_id),
            metadata={
                "source": "mem0",
                "memory_id": mem_id,
                "topic": topic,
                "agent": source,
                "snippet": text[:500],
            },
        ))
    return results


def register_tools(server: FastMCP, *, max_fetch_chars: int) -> None:
    @server.tool(
        name="search",
        title="Search tigermemory",
        description=(
            "Search tigermemory wiki/sources plus recent Mem0 memories. "
            "Use fetch with a returned id before citing or relying on a result."
        ),
        annotations=READ_ONLY_TOOL,
        meta=_tool_meta(),
        structured_output=True,
    )
    def search(query: str) -> SearchResponse:
        q = (query or "").strip()
        if not q:
            raise ValueError("query must be non-empty")
        extra_results = _search_extra_doc_results(q, 3)
        wiki_results = _search_wiki_results(q, 8)
        if _should_fast_path_extra_docs(q, extra_results):
            results = [*extra_results, *wiki_results]
        else:
            results = [*wiki_results, *extra_results]
        results.extend(_search_mem0_results(q, 5))
        return SearchResponse(results=results[:12])

    @server.tool(
        name="fetch",
        title="Fetch tigermemory document",
        description="Fetch a full tigermemory document or memory by id returned from search.",
        annotations=READ_ONLY_TOOL,
        meta=_tool_meta(),
        structured_output=True,
    )
    def fetch(id: str) -> FetchResponse:
        item_id = (id or "").strip()
        if item_id.startswith("page:"):
            path = _decode_page_id(item_id)
            full = _safe_text_file(path)
            text = full.read_text(encoding="utf-8")
            truncated = len(text) > max_fetch_chars
            if truncated:
                text = text[:max_fetch_chars]
            return FetchResponse(
                id=item_id,
                title=_title_from_text(path, text),
                text=text,
                url=_page_url(path),
                metadata={"source": "repo", "path": path, "truncated": truncated},
            )
        if item_id.startswith("mem0:"):
            mem_id = _mem0_id(item_id)
            url = f"{tm_core.mem0_base().rstrip('/')}/api/v1/memories/{urllib.parse.quote(mem_id)}"
            data = json.loads(tm_core.mem0_request(url))
            text = str(data.get("text") or data.get("content") or data.get("memory") or "")
            metadata = data.get("metadata_") or data.get("metadata") or {}
            return FetchResponse(
                id=item_id,
                title=f"Memory: {metadata.get('topic', 'unknown')} / {metadata.get('source', 'unknown')}",
                text=text,
                url=_mem0_url(mem_id),
                metadata={"source": "mem0", "memory_id": mem_id, **metadata},
            )
        raise ValueError("id must be a value returned by search")

    @server.tool(
        name="get_agent_onboarding",
        title="Get tigermemory onboarding",
        description="Return the read-only tigermemory operating snapshot for agents using this memory system.",
        annotations=READ_ONLY_TOOL,
        meta=_tool_meta(),
        structured_output=True,
    )
    def get_agent_onboarding(depth: Literal["30s", "5min", "full"] = "30s") -> OnboardingResponse:
        content = tm_persona.compile_snapshot(depth)
        return OnboardingResponse(depth=depth, content=content, sources=list(tm_persona.SOURCE_PATHS))

    @server.tool(
        name="write_memory",
        title="Write tigermemory memory",
        description=(
            "Write a durable note through tigermemory's server-side router. "
            "The service may store it in Mem0, route it to inbox for review, or discard it. "
            "This does not grant direct wiki, source, admin, media, expense, or filesystem writes."
        ),
        annotations=WRITE_MEMORY_TOOL,
        meta=_tool_meta([WRITE_MEMORY_SCOPE]),
        structured_output=True,
    )
    def write_memory(
        topic: Literal[
            "brand",
            "investment",
            "operations",
            "production",
            "systems",
            "person",
            "selfevolution",
            "cross",
        ],
        text: str,
        ctx: Context,
    ) -> WriteMemoryResponse:
        _require_write_memory_scope(ctx)
        return _write_memory_via_router(topic, text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdio", action="store_true", default=True, help="Use stdio transport (default)")
    ap.add_argument("--http", action="store_true", help="Use HTTP transport")
    ap.add_argument("--host", default="0.0.0.0", help="HTTP host")
    ap.add_argument("--port", type=int, default=9776, help="HTTP port")
    ap.add_argument("--auth", choices=["oauth", "none"], default="oauth", help="HTTP auth mode")
    ap.add_argument("--public-base", default=os.environ.get("TM_OPENAI_MCP_PUBLIC_BASE", DEFAULT_PUBLIC_BASE))
    ap.add_argument("--oauth-store", default=os.environ.get("TM_OPENAI_MCP_OAUTH_STORE", str(DEFAULT_STORE_PATH)))
    ap.add_argument("--max-fetch-chars", type=int, default=int(os.environ.get(
        "TM_OPENAI_MCP_MAX_FETCH_CHARS", str(DEFAULT_MAX_FETCH_CHARS)
    )))
    args = ap.parse_args()

    try:
        server = _build_mcp(
            auth_mode=args.auth if args.http else "none",
            public_base=args.public_base,
            link_secret=os.environ.get("TM_OPENAI_MCP_LINK_SECRET"),
            store_path=pathlib.Path(args.oauth_store),
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    register_tools(server, max_fetch_chars=args.max_fetch_chars)

    if args.http:
        import uvicorn
        uvicorn.run(server.streamable_http_app(), host=args.host, port=args.port, log_level="info")
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
