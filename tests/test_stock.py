"""wkvideo assets: Pexels / Pixabay with every network call mocked."""

import io
import json
import urllib.error
import urllib.parse

import pytest
from PIL import Image

from wkvideo import stock
from wkvideo.stock import (
    Asset, Pexels, Pixabay, SearchCache, StockError, choose_provider, fill_scenes, mentions_brand, pick,
    pick_rendition,
)

PIXABAY_KEY = "pixabay-secret-123"
PEXELS_KEY = "pexels-secret-456"


def jpeg_bytes(size=(720, 1280)):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def pixabay_image(id, tags="phone, message", w=3000, h=4500):
    return {"id": id, "pageURL": f"https://pixabay.com/photos/sample-{id}/", "tags": tags, "user": f"user{id}",
            "imageWidth": w, "imageHeight": h, "largeImageURL": f"https://cdn.pixabay.test/{id}_1280.jpg"}


def pixabay_video(id, renditions, tags="phone, hands", duration=12):
    return {"id": id, "pageURL": f"https://pixabay.com/videos/id-{id}/", "tags": tags, "user": f"user{id}",
            "duration": duration,
            "videos": {name: {"url": f"https://cdn.pixabay.test/{id}_{name}.mp4", "width": w, "height": h,
                              "size": 1000} for name, (w, h) in renditions.items()}}


class FakeNet:
    """Routes URLs to canned responses and records every request."""

    def __init__(self, images=(), videos=(), pexels=None):
        self.calls = []
        self.images = list(images)
        self.videos = list(videos)
        self.pexels = pexels or {}

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        parts = urllib.parse.urlsplit(url)
        if parts.netloc == "pixabay.com" and parts.path == "/api/":
            return json.dumps({"total": len(self.images), "hits": self.images}).encode()
        if parts.netloc == "pixabay.com" and parts.path == "/api/videos/":
            return json.dumps({"total": len(self.videos), "hits": self.videos}).encode()
        if parts.netloc == "api.pexels.com":
            return json.dumps(self.pexels.get(parts.path, {})).encode()
        if parts.path.endswith(".mp4"):
            return b"\x00\x00\x00\x18ftypmp42fake-video"
        return jpeg_bytes()

    def api_calls(self):
        return [c for c in self.calls if "/api" in c[0] or "api.pexels.com" in c[0]]


@pytest.fixture
def cache(tmp_path):
    return SearchCache(tmp_path / "cache")


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)


# --- provider choice -------------------------------------------------------

def test_pexels_is_used_when_its_key_is_set(monkeypatch, cache):
    monkeypatch.setenv("PEXELS_API_KEY", PEXELS_KEY)
    monkeypatch.setenv("PIXABAY_API_KEY", PIXABAY_KEY)
    assert isinstance(choose_provider(cache), Pexels)


def test_pixabay_when_pexels_key_is_empty(monkeypatch, cache):
    monkeypatch.setenv("PEXELS_API_KEY", "  ")
    monkeypatch.setenv("PIXABAY_API_KEY", PIXABAY_KEY)
    assert isinstance(choose_provider(cache), Pixabay)


def test_no_key_at_all_is_an_error(no_keys, cache):
    with pytest.raises(StockError, match="PEXELS_API_KEY or PIXABAY_API_KEY"):
        choose_provider(cache)


def test_provider_repr_hides_the_key(cache):
    assert PIXABAY_KEY not in repr(Pixabay(PIXABAY_KEY, cache)) + str(Pixabay(PIXABAY_KEY, cache))


# --- Pixabay ---------------------------------------------------------------

def test_pixabay_image_search_asks_for_vertical_photos(cache):
    net = FakeNet(images=[pixabay_image(1)])
    found = Pixabay(PIXABAY_KEY, cache, net).images("phone message")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(net.calls[0][0]).query)
    assert query["orientation"] == ["vertical"] and query["image_type"] == ["photo"]
    assert query["q"] == ["phone message"]
    assert found[0].url == "https://cdn.pixabay.test/1_1280.jpg" and found[0].vertical


def test_pixabay_results_are_cached_for_24_hours(tmp_path):
    now = [1_000_000.0]
    cache = SearchCache(tmp_path / "c", clock=lambda: now[0])
    net = FakeNet(images=[pixabay_image(1)])
    px = Pixabay(PIXABAY_KEY, cache, net)
    px.images("phone")
    now[0] += 23 * 3600
    px.images("phone")
    assert len(net.api_calls()) == 1 and px.requests == 1
    now[0] += 3600  # 24h after the first request
    px.images("phone")
    assert len(net.api_calls()) == 2


def test_cache_is_per_query_and_kind(cache):
    net = FakeNet(images=[pixabay_image(1)], videos=[])
    px = Pixabay(PIXABAY_KEY, cache, net)
    px.images("phone")
    px.images("laptop")
    px.videos("phone")
    assert len(net.api_calls()) == 3


def test_cache_survives_a_new_provider_instance(cache):
    net = FakeNet(images=[pixabay_image(1)])
    Pixabay(PIXABAY_KEY, cache, net).images("phone")
    Pixabay(PIXABAY_KEY, cache, net).images("phone")
    assert len(net.api_calls()) == 1


def test_cache_files_never_contain_the_key(cache):
    net = FakeNet(images=[pixabay_image(1)], videos=[pixabay_video(2, {"large": (1080, 1920)})])
    px = Pixabay(PIXABAY_KEY, cache, net)
    px.images("phone")
    px.videos("phone")
    files = list(cache.dir.iterdir())
    assert files
    for f in files:
        assert PIXABAY_KEY not in f.name and PIXABAY_KEY not in f.read_text(encoding="utf-8")


def test_pixabay_keeps_only_vertical_videos(cache):
    net = FakeNet(videos=[
        pixabay_video(1, {"large": (1920, 1080), "medium": (1280, 720)}),       # landscape
        pixabay_video(2, {"large": (1080, 1920), "medium": (720, 1280)}),       # vertical
        pixabay_video(3, {"large": (1080, 1080)}),                               # square
    ])
    found = Pixabay(PIXABAY_KEY, cache, net).videos("phone")
    assert [a.id for a in found] == ["2"]
    assert (found[0].width, found[0].height) == (1080, 1920)
    assert found[0].url.endswith("2_large.mp4")


def test_pick_rendition_prefers_largest_vertical_up_to_4k():
    files = [{"link": "a", "width": 720, "height": 1280}, {"link": "b", "width": 1080, "height": 1920},
             {"link": "c", "width": 2160, "height": 3840}, {"link": "d", "width": 3840, "height": 2160}]
    assert pick_rendition(files)["link"] == "b"
    assert pick_rendition([files[2]])["link"] == "c"  # only an oversized one: still usable
    assert pick_rendition([files[3]]) is None


def test_pixabay_skips_renditions_without_a_url(cache):
    video = pixabay_video(1, {"large": (2160, 3840), "medium": (1080, 1920)})
    video["videos"]["large"]["url"] = ""
    found = Pixabay(PIXABAY_KEY, cache, FakeNet(videos=[video])).videos("x")
    assert found[0].url.endswith("1_medium.mp4")


def test_pixabay_error_message_hides_the_key(cache, monkeypatch):
    def fail(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b"[ERROR 400] Invalid or missing API key"))

    monkeypatch.setattr(stock.urllib.request, "urlopen", fail)
    with pytest.raises(StockError) as err:
        Pixabay(PIXABAY_KEY, cache).images("phone")
    assert PIXABAY_KEY not in str(err.value)
    assert "HTTP 400" in str(err.value) and "pixabay.com" in str(err.value)


def test_network_error_message_hides_the_key(cache, monkeypatch):
    def fail(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(stock.urllib.request, "urlopen", fail)
    with pytest.raises(StockError) as err:
        Pixabay(PIXABAY_KEY, cache).images("phone")
    assert PIXABAY_KEY not in str(err.value)


# --- Pexels ----------------------------------------------------------------

PEXELS_RESPONSES = {
    "/v1/search": {"photos": [{
        "id": 11, "url": "https://www.pexels.com/photo/person-holding-phone-11/", "photographer": "Ana",
        "alt": "Person holding a phone", "width": 3000, "height": 4500,
        "src": {"original": "https://images.pexels.test/photos/11/a.jpeg"}}]},
    "/videos/search": {"videos": [{
        "id": 22, "url": "https://www.pexels.com/video/hands-22/", "duration": 9, "user": {"name": "Ben"},
        "video_files": [
            {"link": "https://videos.pexels.test/22-uhd.mp4", "width": 2160, "height": 3840, "file_type": "video/mp4"},
            {"link": "https://videos.pexels.test/22-hd.mp4", "width": 1080, "height": 1920, "file_type": "video/mp4"},
            {"link": "https://videos.pexels.test/22-wide.mp4", "width": 1920, "height": 1080, "file_type": "video/mp4"},
        ]}]},
}


def test_pexels_sends_the_key_as_a_header_not_in_the_url(cache):
    net = FakeNet(pexels=PEXELS_RESPONSES)
    found = Pexels(PEXELS_KEY, cache, net).images("phone")
    url, headers = net.calls[0]
    assert PEXELS_KEY not in url and headers["Authorization"] == PEXELS_KEY
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["orientation"] == ["portrait"]
    assert found[0].author == "Ana" and found[0].url.startswith("https://images.pexels.test/photos/11/a.jpeg?")


def test_pexels_picks_a_vertical_hd_video_file(cache):
    found = Pexels(PEXELS_KEY, cache, FakeNet(pexels=PEXELS_RESPONSES)).videos("hands")
    assert found[0].url == "https://videos.pexels.test/22-hd.mp4" and found[0].duration == 9


# --- picking ---------------------------------------------------------------

def asset(id, text="", page="https://pixabay.com/photos/sample/", w=720, h=1280, duration=0.0):
    return Asset("image", f"https://x/{id}.jpg", w, h, "Pixabay", str(id), page, "u", text, duration)


@pytest.mark.parametrize("text,page,word", [
    ("whatsapp, phone, chat", "", "whatsapp"),
    ("iphone, hand", "", "iphone"),
    ("phone, hand", "https://pixabay.com/photos/samsung-galaxy-phone-123/", "samsung"),
    ("social media logo", "", "logo"),
    ("phone, hand, message", "https://pixabay.com/photos/hand-phone-123/", None),
    ("metal, metaphor", "", None),   # 'meta' only as a whole word
])
def test_mentions_brand(text, page, word):
    assert mentions_brand(asset(1, text, page or "https://pixabay.com/photos/x-1/")) == word


def test_pick_skips_brand_results_when_another_is_available():
    chosen, notes = pick([asset(1, "whatsapp, chat"), asset(2, "chat, hand")])
    assert chosen.id == "2" and "whatsapp" in notes[0]


def test_pick_falls_back_to_a_brand_result_and_says_so():
    chosen, notes = pick([asset(1, "apple, iphone"), asset(2, "samsung")])
    assert chosen.id == "1" and "check it for logos" in notes[-1]


def test_pick_skips_used_and_horizontal_assets():
    chosen, _ = pick([asset(1), asset(2, w=1920, h=1080), asset(3)], used=["1"])
    assert chosen.id == "3"
    assert pick([asset(1, w=1920, h=1080)])[0] is None


def test_pick_prefers_videos_long_enough_for_the_scene():
    chosen, _ = pick([asset(1, duration=3), asset(2, duration=8)], min_duration=5)
    assert chosen.id == "2"
    assert pick([asset(1, duration=3)], min_duration=5)[0].id == "1"  # loops if nothing is long enough


# --- fill_scenes -----------------------------------------------------------

def write_scenes(folder, scenes):
    path = folder / "scenes.json"
    path.write_text(json.dumps(scenes), encoding="utf-8")
    return path


def test_fill_scenes_downloads_and_records_credits(tmp_path, cache):
    path = write_scenes(tmp_path, [
        {"image_query": "phone message", "top": "a"},
        {"video_query": "hands typing", "top": "b"},
        {"image": "own.png", "top": "c"},
    ])
    net = FakeNet(images=[pixabay_image(1, tags="whatsapp, phone"), pixabay_image(2)],
                  videos=[pixabay_video(3, {"large": (1080, 1920)})])
    logs = []
    picked = fill_scenes(path, Pixabay(PIXABAY_KEY, cache, net), net, log=logs.append)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["image"] == "01.jpg" and data[0]["credit"]["id"] == "2"   # brand result skipped
    assert data[0]["credit"]["page"] == "https://pixabay.com/photos/sample-2/"
    assert data[1]["video"] == "02.mp4" and data[1]["credit"]["duration"] == 12
    assert data[2] == {"image": "own.png", "top": "c"}
    assert (tmp_path / "01.jpg").stat().st_size > 0 and (tmp_path / "02.mp4").is_file()
    assert [p.file for p in picked] == ["01.jpg", "02.mp4"]
    assert any("720x1280" in line for line in logs)  # the downloaded size, not the original's
    assert PIXABAY_KEY not in path.read_text(encoding="utf-8") + "\n".join(logs)


def test_fill_scenes_does_not_fetch_again_when_files_exist(tmp_path, cache):
    path = write_scenes(tmp_path, [{"image_query": "phone"}])
    net = FakeNet(images=[pixabay_image(1)])
    fill_scenes(path, Pixabay(PIXABAY_KEY, cache, net), net, log=lambda m: None)
    before = len(net.calls)
    assert fill_scenes(path, Pixabay(PIXABAY_KEY, cache, net), net, log=lambda m: None) == []
    assert len(net.calls) == before


def test_force_picks_a_different_asset_from_the_cache(tmp_path, cache):
    path = write_scenes(tmp_path, [{"image_query": "phone"}])
    net = FakeNet(images=[pixabay_image(1), pixabay_image(2)])
    px = Pixabay(PIXABAY_KEY, cache, net)
    fill_scenes(path, px, net, log=lambda m: None)
    fill_scenes(path, px, net, force=True, log=lambda m: None)
    assert json.loads(path.read_text(encoding="utf-8"))[0]["credit"]["id"] == "2"
    assert len(net.api_calls()) == 1  # the second search came from the cache


def test_scenes_never_reuse_an_asset(tmp_path, cache):
    path = write_scenes(tmp_path, [{"image_query": "phone"}, {"image_query": "phone"}])
    net = FakeNet(images=[pixabay_image(1), pixabay_image(2)])
    fill_scenes(path, Pixabay(PIXABAY_KEY, cache, net), net, log=lambda m: None)
    ids = [s["credit"]["id"] for s in json.loads(path.read_text(encoding="utf-8"))]
    assert ids == ["1", "2"]


def test_no_vertical_video_is_an_error(tmp_path, cache):
    path = write_scenes(tmp_path, [{"video_query": "phone"}])
    net = FakeNet(videos=[pixabay_video(1, {"large": (1920, 1080)})])
    with pytest.raises(StockError, match="no vertical video"):
        fill_scenes(path, Pixabay(PIXABAY_KEY, cache, net), net, log=lambda m: None)


def test_assets_cli_uses_pixabay_and_hides_the_key(tmp_path, monkeypatch, capsys):
    # Empty, not unset, so monkeypatch restores whatever load_env fills in.
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "ep"
    folder.mkdir()
    (folder / ".env").write_text(f"PEXELS_API_KEY=\nPIXABAY_API_KEY={PIXABAY_KEY}\n", encoding="utf-8")
    write_scenes(folder, [{"image_query": "phone"}])
    net = FakeNet(images=[pixabay_image(1)])
    from wkvideo.cli import main

    assert stock.main([str(folder), "--cache-dir", str(tmp_path / "cache")], fetch=net) == 0
    out = capsys.readouterr()
    assert "Using Pixabay" in out.out and PIXABAY_KEY not in out.out + out.err
    assert main(["assets", str(folder), "--cache-dir", str(tmp_path / "cache")]) == 0  # dispatch works


def test_assets_cli_reports_a_missing_key(tmp_path, monkeypatch, capsys):
    # Empty, not unset, so monkeypatch restores whatever load_env fills in.
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    write_scenes(tmp_path, [{"image_query": "phone"}])
    assert stock.main([str(tmp_path), "--cache-dir", str(tmp_path / "c")], fetch=FakeNet()) == 1
    assert "PIXABAY_API_KEY" in capsys.readouterr().err
