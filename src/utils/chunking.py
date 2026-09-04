"""HTML cleanup + paragraph-aware, token-budgeted chunking for LLM payloads.

Goal: never trip a provider's 413, without just truncating at an arbitrary
character count. We (1) strip boilerplate, (2) find the main content block,
(3) split on paragraph boundaries, (4) pack paragraphs into token-budgeted
chunks with a small overlap so context isn't lost at a chunk seam, and
(5) always keep the title/lead paragraph in every chunk since that's where
the semantically densest information usually lives.
"""
from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

_BOILERPLATE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript"]
_BOILERPLATE_CLASS_HINTS = [
    "nav", "footer", "sidebar", "advert", "ad-", "cookie", "subscribe", "newsletter",
    "social-share", "comment", "related-posts", "breadcrumb", "menu",
]


def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic estimate: ~4 chars/token for English text.
    Deliberately conservative (rounds up) so we underfill rather than overfill
    a budget.
    """
    return max(1, (len(text) + 3) // 4)


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag_name in _BOILERPLATE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Two passes deliberately: find_all(True) materializes a flat list that
    # includes both ancestors and descendants. Decomposing a tag mid-loop
    # tears down its subtree (bs4 clears the decomposed tag's internal
    # state), so a later iteration hitting one of those now-decomposed
    # descendants crashes with "'NoneType' object has no attribute 'get'".
    # Collecting matches first and decomposing after keeps every `tag` in
    # the scan loop live for the whole scan.
    to_remove = []
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []) or []) + " " + (tag.get("id") or "")
        classes = classes.lower()
        if any(hint in classes for hint in _BOILERPLATE_CLASS_HINTS):
            to_remove.append(tag)
    for tag in to_remove:
        if tag.parent is not None:  # already removed as a descendant of an earlier match
            tag.decompose()

    main = soup.find("article") or soup.find(attrs={"role": "main"}) or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n") if main else soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n\n".join(lines)


def split_paragraphs(text: str) -> list[str]:
    raw_paragraphs = [p.strip() for p in text.split("\n\n")]
    return [p for p in raw_paragraphs if p]


@dataclass
class Chunk:
    index: int
    text: str
    token_estimate: int
    is_lead: bool


def chunk_text(
    text: str,
    token_budget: int = 3500,
    overlap_tokens: int = 200,
    always_include_lead: bool = True,
) -> list[Chunk]:
    """Pack paragraphs into chunks that each fit under `token_budget`.

    The lead paragraph (title/first paragraph — usually the highest
    information density in an article) is prepended to every chunk when
    `always_include_lead` is set, so a fallback provider working on chunk 3
    still has the headline context even without chunk 1.
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    lead = paragraphs[0]
    lead_tokens = estimate_tokens(lead)

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0
    start_idx = 1 if always_include_lead else 0

    def flush(is_first: bool) -> None:
        nonlocal current, current_tokens
        if not current:
            return
        body = "\n\n".join(current)
        full_text = f"{lead}\n\n{body}" if (always_include_lead and not is_first) else body
        if is_first and always_include_lead:
            full_text = f"{lead}\n\n{body}" if body else lead
        chunks.append(
            Chunk(index=len(chunks), text=full_text, token_estimate=estimate_tokens(full_text), is_lead=is_first)
        )
        current = []
        current_tokens = 0

    effective_budget = max(token_budget - (lead_tokens if always_include_lead else 0), 500)

    for para in paragraphs[start_idx:]:
        p_tokens = estimate_tokens(para)
        if current_tokens + p_tokens > effective_budget and current:
            flush(is_first=(len(chunks) == 0))
            if overlap_tokens > 0 and current == [] and chunks:
                overlap_text = _tail_by_tokens(chunks[-1].text, overlap_tokens)
                if overlap_text:
                    current.append(overlap_text)
                    current_tokens += estimate_tokens(overlap_text)
        current.append(para)
        current_tokens += p_tokens

    flush(is_first=(len(chunks) == 0))

    if not chunks:
        chunks.append(Chunk(index=0, text=lead, token_estimate=lead_tokens, is_lead=True))
    return chunks


def _tail_by_tokens(text: str, token_count: int) -> str:
    char_count = token_count * 4
    return text[-char_count:] if len(text) > char_count else text


def shrink_for_413(chunks: list[Chunk], text: str, current_budget: int, overlap_tokens: int) -> list[Chunk]:
    """Called when a provider returns 413 even after normal chunking (e.g. a
    stricter provider limit than we assumed). Halves the token budget and
    re-chunks; caller retries with the new, smaller chunks.
    """
    new_budget = max(current_budget // 2, 500)
    return chunk_text(text, token_budget=new_budget, overlap_tokens=min(overlap_tokens, new_budget // 4))
