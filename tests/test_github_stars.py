from aioresponses import aioresponses

from src.extractors.github_stars import GithubStarsCache, GithubStarsClient
from src.utils.http_client import AsyncHttpClient
from src.utils.retry import BackoffConfig


def test_parse_repo_slug_from_various_url_forms():
    assert GithubStarsClient.parse_repo_slug("https://github.com/openai/gpt-3") == "openai/gpt-3"
    assert GithubStarsClient.parse_repo_slug("http://github.com/foo/bar.git") == "foo/bar"
    assert GithubStarsClient.parse_repo_slug("https://github.com/foo/bar/tree/main") == "foo/bar"
    assert GithubStarsClient.parse_repo_slug("https://example.com/not-github") is None


async def test_get_stars_success_and_cache_hit(tmp_path):
    cache = GithubStarsCache(tmp_path / "cache.json")
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=1)) as http:
        client = GithubStarsClient(http, token="", cache=cache)
        with aioresponses() as m:
            m.get(
                "https://api.github.com/repos/foo/bar",
                payload={"stargazers_count": 1234},
                status=200,
                headers={"X-RateLimit-Remaining": "59"},
            )
            stars = await client.get_stars("https://github.com/foo/bar")
        assert stars == 1234

        # Second call must hit the cache, not the network — no mock registered
        # this time, so any real HTTP attempt would raise inside aioresponses.
        with aioresponses():
            stars_again = await client.get_stars("https://github.com/foo/bar")
        assert stars_again == 1234


async def test_get_stars_404_caches_none(tmp_path):
    cache = GithubStarsCache(tmp_path / "cache.json")
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=1)) as http:
        client = GithubStarsClient(http, token="", cache=cache)
        with aioresponses() as m:
            m.get("https://api.github.com/repos/foo/ghost-repo", status=404, headers={"X-RateLimit-Remaining": "58"})
            stars = await client.get_stars("https://github.com/foo/ghost-repo")
        assert stars is None
        assert cache.has("foo/ghost-repo") is True


async def test_get_stars_quota_exhausted_stops_making_requests(tmp_path):
    cache = GithubStarsCache(tmp_path / "cache.json")
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=0)) as http:
        client = GithubStarsClient(http, token="", cache=cache)
        with aioresponses() as m:
            m.get("https://api.github.com/repos/foo/bar1", status=403)
            stars1 = await client.get_stars("https://github.com/foo/bar1")
        assert stars1 is None
        assert client._quota_exhausted is True

        # No mock registered for bar2 — proves the client skips the network
        # call entirely once quota is known to be exhausted.
        with aioresponses():
            stars2 = await client.get_stars("https://github.com/foo/bar2")
        assert stars2 is None


async def test_get_stars_no_repo_url_returns_none(tmp_path):
    cache = GithubStarsCache(tmp_path / "cache.json")
    async with AsyncHttpClient() as http:
        client = GithubStarsClient(http, token="", cache=cache)
        assert await client.get_stars("https://example.com/not-a-repo") is None


def test_cache_persists_to_disk_and_reloads(tmp_path):
    path = tmp_path / "cache.json"
    cache1 = GithubStarsCache(path)
    cache1.set("foo/bar", 42)

    cache2 = GithubStarsCache(path)  # simulates a fresh process reading the same cache file
    assert cache2.has("foo/bar") is True
    assert cache2.get_cached("foo/bar") == 42
