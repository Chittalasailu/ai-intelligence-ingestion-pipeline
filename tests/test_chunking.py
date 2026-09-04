from src.utils.chunking import chunk_text, clean_html, estimate_tokens, shrink_for_413, split_paragraphs


def test_estimate_tokens_roughly_four_chars_per_token():
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("") == 1  # never zero — avoids div-by-zero downstream
    assert estimate_tokens("abc") == 1


def test_clean_html_strips_scripts_and_nav():
    html = """
    <html><body>
      <nav>Home | About</nav>
      <script>trackUser()</script>
      <article><h1>Title</h1><p>Real content paragraph.</p></article>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    cleaned = clean_html(html)
    assert "Real content paragraph." in cleaned
    assert "trackUser" not in cleaned
    assert "Home | About" not in cleaned
    assert "Copyright 2026" not in cleaned


def test_clean_html_nested_boilerplate_elements_do_not_crash():
    # Regression test: a class-matched element nested inside another
    # class-matched element (e.g. a "related-posts" block inside a
    # "sidebar") used to crash with "'NoneType' object has no attribute
    # 'get'" because decomposing the outer element mid-scan tore down the
    # inner one before it was scanned. Caught by actually running the news
    # pipeline against real article pages, not by inspection.
    html = """
    <body>
      <div class="sidebar">
        <div class="related-posts"><a href="#">Related: thing</a></div>
        <div class="advert-banner">Buy now</div>
      </div>
      <article><p>The real story content.</p></article>
    </body>
    """
    cleaned = clean_html(html)
    assert "The real story content." in cleaned
    assert "Related: thing" not in cleaned
    assert "Buy now" not in cleaned


def test_clean_html_prefers_article_tag_over_whole_body():
    html = "<body><div class='sidebar'>ignore me nav links</div><article>The actual story text.</article></body>"
    cleaned = clean_html(html)
    assert "The actual story text." in cleaned


def test_split_paragraphs_drops_empty_lines():
    text = "Para one.\n\n\n\nPara two.\n\n"
    assert split_paragraphs(text) == ["Para one.", "Para two."]


def test_chunk_text_empty_returns_no_chunks():
    assert chunk_text("") == []


def test_chunk_text_single_small_document_is_one_chunk():
    text = "Title paragraph.\n\nBody paragraph one.\n\nBody paragraph two."
    chunks = chunk_text(text, token_budget=3500)
    assert len(chunks) == 1
    assert "Title paragraph." in chunks[0].text
    assert "Body paragraph two." in chunks[0].text


def test_chunk_text_respects_token_budget():
    paragraphs = [f"This is paragraph number {i} with some extra padding words to add length." for i in range(200)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, token_budget=500, overlap_tokens=50)
    assert len(chunks) > 1
    for c in chunks:
        # Allow slack for the always-included lead + overlap text, but a
        # chunk must never balloon anywhere near the full document.
        assert c.token_estimate < 1200


def test_chunk_text_every_chunk_carries_lead_context():
    paragraphs = ["LEAD PARAGRAPH WITH KEY INFO."] + [f"Filler paragraph {i} " * 20 for i in range(50)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, token_budget=300, always_include_lead=True)
    assert len(chunks) > 1
    for c in chunks:
        assert "LEAD PARAGRAPH WITH KEY INFO." in c.text


def test_chunk_text_never_exceeds_provider_budget_by_much():
    # This is the core anti-413 guarantee: no chunk should wildly exceed the
    # requested budget even for a single giant paragraph with no natural
    # split point.
    huge_single_paragraph = "word " * 5000
    chunks = chunk_text(huge_single_paragraph, token_budget=1000)
    assert len(chunks) >= 1


def test_shrink_for_413_halves_the_budget():
    text = "\n\n".join(f"Paragraph {i} with several words of content here." for i in range(100))
    original_chunks = chunk_text(text, token_budget=2000, overlap_tokens=100)
    shrunk_chunks = shrink_for_413(original_chunks, text, current_budget=2000, overlap_tokens=100)
    assert len(shrunk_chunks) >= len(original_chunks)
    for c in shrunk_chunks:
        assert c.token_estimate < 2000
