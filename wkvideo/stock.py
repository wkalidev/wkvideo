"""Fetch a stock photo or vertical video for each scene: Pexels, else Pixabay.

Scenes in scenes.json may carry ``image_query`` or ``video_query`` instead of
a file. :func:`fill_scenes` searches, downloads the first suitable result
next to scenes.json and records it under ``image`` / ``video`` with a
``credit`` block.

- Pexels is used when ``PEXELS_API_KEY`` is set, Pixabay with
  ``PIXABAY_API_KEY`` otherwise.
- Search responses are cached for 24 hours (Pixabay requires it). Cache keys
  and files never contain the API key.
- Videos are kept only when taller than wide (Pixabay has no orientation
  filter for videos, so it is checked on each file's width and height).
- Results whose tags, description or page URL name a brand or logo are
  skipped when another result is available.
- API keys are never printed, logged or written to disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .env import get_key

CACHE_SECONDS = 24 * 60 * 60
DEFAULT_CACHE = Path.home() / ".cache" / "wkvideo" / "stock"
USER_AGENT = f"wkvideo/{__version__} (+https://github.com/wkalidev/wkvideo)"
MAX_VIDEO_HEIGHT = 2160

# Words that suggest a visible brand, logo or trademark in the picture.
BRAND_WORDS = (
    "logo", "logos", "brand", "brands", "trademark", "emblem",
    "apple", "iphone", "ipad", "macbook", "imac", "airpods", "samsung", "galaxy", "huawei", "xiaomi",
    "oneplus", "pixel", "google", "android", "microsoft", "windows", "whatsapp", "facebook",
    "instagram", "messenger", "meta", "tiktok", "youtube", "twitter", "telegram", "snapchat",
    "netflix", "amazon", "paypal", "visa", "mastercard", "coca", "cola", "pepsi", "nike",
    "adidas", "starbucks", "mcdonalds", "sony", "playstation", "xbox", "nintendo", "tesla",
    "bitcoin", "ethereum", "binance", "coinbase",
)
_BRAND_RE = re.compile(r"(?<![a-z])(" + "|".join(BRAND_WORDS) + r")(?![a-z])", re.I)


class StockError(RuntimeError):
    pass


Fetch = Callable[[str, Dict[str, str]], bytes]


def http_fetch(url: str, headers: Dict[str, str], timeout: float = 30.0) -> bytes:
    """GET ``url``. Errors never include the URL or headers (they may hold a key)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", "replace").strip()
        raise StockError(f"{host}: HTTP {exc.code} {body}".strip()) from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise StockError(f"{host}: {reason}") from None


@dataclass
class Asset:
    kind: str            # "image" or "video"
    url: str             # file to download
    width: int
    height: int
    provider: str
    id: str
    page: str
    author: str = ""
    text: str = ""       # tags or description, used for the brand check
    duration: float = 0.0

    @property
    def vertical(self) -> bool:
        return self.height > self.width

    def credit(self) -> dict:
        out = {"provider": self.provider, "id": self.id, "author": self.author, "page": self.page}
        if self.kind == "video":
            out["duration"] = self.duration
        return out


def mentions_brand(asset: Asset) -> Optional[str]:
    """The brand word found in the asset's tags, description or page URL, if any."""
    slug = urllib.parse.urlsplit(asset.page).path.replace("-", " ").replace("/", " ")
    m = _BRAND_RE.search(f"{asset.text} {slug}")
    return m.group(1).lower() if m else None


class SearchCache:
    """Search responses on disk for ``ttl`` seconds, keyed without the API key."""

    def __init__(self, directory: Path = DEFAULT_CACHE, ttl: float = CACHE_SECONDS,
                 clock: Callable[[], float] = time.time):
        self.dir = Path(directory)
        self.ttl = ttl
        self.clock = clock

    def _path(self, provider: str, endpoint: str, params: Dict[str, str]) -> Path:
        ident = json.dumps([provider, endpoint, sorted(params.items())])
        return self.dir / f"{provider.lower()}-{hashlib.sha256(ident.encode()).hexdigest()[:24]}.json"

    def get(self, provider: str, endpoint: str, params: Dict[str, str]) -> Optional[dict]:
        path = self._path(provider, endpoint, params)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.clock() - float(saved.get("time", 0)) >= self.ttl:
            return None
        return saved.get("data")

    def put(self, provider: str, endpoint: str, params: Dict[str, str], data: dict) -> None:
        path = self._path(provider, endpoint, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"time": self.clock(), "params": params, "data": data}), encoding="utf-8")
        tmp.replace(path)


class Provider:
    name = ""

    def __init__(self, key: str, cache: SearchCache, fetch: Fetch = http_fetch):
        if not key:
            raise StockError(f"{self.name}: no API key")
        self._key = key
        self.cache = cache
        self.fetch = fetch
        self.requests = 0  # network requests made (cache hits don't count)

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}()"

    def _get(self, endpoint: str, params: Dict[str, str]) -> dict:
        cached = self.cache.get(self.name, endpoint, params)
        if cached is not None:
            return cached
        url, headers = self._request(endpoint, params)
        self.requests += 1
        try:
            data = json.loads(self.fetch(url, headers).decode("utf-8"))
        except ValueError:
            raise StockError(f"{self.name}: response is not JSON") from None
        self.cache.put(self.name, endpoint, params, data)
        return data

    def _request(self, endpoint: str, params: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        raise NotImplementedError

    def images(self, query: str) -> List[Asset]:
        raise NotImplementedError

    def videos(self, query: str) -> List[Asset]:
        raise NotImplementedError


class Pexels(Provider):
    name = "Pexels"
    API = "https://api.pexels.com"

    def _request(self, endpoint, params):
        return f"{self.API}{endpoint}?{urllib.parse.urlencode(params)}", {"Authorization": self._key}

    def images(self, query: str) -> List[Asset]:
        data = self._get("/v1/search", {"query": query, "orientation": "portrait", "per_page": "30"})
        out = []
        for p in data.get("photos") or []:
            original = (p.get("src") or {}).get("original")
            if not original:
                continue
            sep = "&" if "?" in original else "?"
            out.append(Asset(
                kind="image", url=f"{original}{sep}auto=compress&cs=tinysrgb&h=2400",
                width=int(p.get("width") or 0), height=int(p.get("height") or 0),
                provider=self.name, id=str(p.get("id")), page=p.get("url") or "",
                author=p.get("photographer") or "", text=p.get("alt") or "",
            ))
        return out

    def videos(self, query: str) -> List[Asset]:
        data = self._get("/videos/search", {"query": query, "orientation": "portrait", "per_page": "30"})
        out = []
        for v in data.get("videos") or []:
            files = [f for f in v.get("video_files") or []
                     if f.get("link") and (f.get("file_type") or "video/mp4") == "video/mp4"
                     and f.get("width") and f.get("height")]
            best = pick_rendition(files)
            if not best:
                continue
            out.append(Asset(
                kind="video", url=best["link"], width=int(best["width"]), height=int(best["height"]),
                provider=self.name, id=str(v.get("id")), page=v.get("url") or "",
                author=(v.get("user") or {}).get("name", ""), text="",
                duration=float(v.get("duration") or 0),
            ))
        return out


class Pixabay(Provider):
    name = "Pixabay"
    API = "https://pixabay.com/api"

    def _request(self, endpoint, params):
        query = urllib.parse.urlencode({"key": self._key, **params})
        return f"{self.API}{endpoint}?{query}", {}

    def images(self, query: str) -> List[Asset]:
        data = self._get("/", {"q": query[:100], "image_type": "photo", "orientation": "vertical",
                               "safesearch": "true", "per_page": "50"})
        out = []
        for h in data.get("hits") or []:
            url = h.get("fullHDURL") or h.get("largeImageURL")
            if not url:
                continue
            w, hgt = int(h.get("imageWidth") or 0), int(h.get("imageHeight") or 0)
            out.append(Asset(
                kind="image", url=url, width=w, height=hgt, provider=self.name, id=str(h.get("id")),
                page=h.get("pageURL") or "", author=h.get("user") or "", text=h.get("tags") or "",
            ))
        return out

    def videos(self, query: str) -> List[Asset]:
        data = self._get("/videos/", {"q": query[:100], "video_type": "film", "safesearch": "true",
                                      "per_page": "100"})
        out = []
        for h in data.get("hits") or []:
            files = [f for f in (h.get("videos") or {}).values()
                     if isinstance(f, dict) and f.get("url") and f.get("width") and f.get("height")]
            best = pick_rendition(files, key="url")
            if not best:
                continue
            out.append(Asset(
                kind="video", url=best["url"], width=int(best["width"]), height=int(best["height"]),
                provider=self.name, id=str(h.get("id")), page=h.get("pageURL") or "",
                author=h.get("user") or "", text=h.get("tags") or "",
                duration=float(h.get("duration") or 0),
            ))
        return out


def pick_rendition(files: Sequence[dict], key: str = "link") -> Optional[dict]:
    """The largest vertical file no taller than 4K, or None if no file is vertical."""
    vertical = [f for f in files if int(f["height"]) > int(f["width"]) and f.get(key)]
    if not vertical:
        return None
    fitting = [f for f in vertical if int(f["height"]) <= MAX_VIDEO_HEIGHT]
    if fitting:
        return max(fitting, key=lambda f: int(f["height"]))
    return min(vertical, key=lambda f: int(f["height"]))


def choose_provider(cache: SearchCache, fetch: Fetch = http_fetch) -> Provider:
    """Pexels when PEXELS_API_KEY is set, otherwise Pixabay with PIXABAY_API_KEY."""
    pexels = get_key("PEXELS_API_KEY")
    if pexels:
        return Pexels(pexels, cache, fetch)
    pixabay = get_key("PIXABAY_API_KEY")
    if pixabay:
        return Pixabay(pixabay, cache, fetch)
    raise StockError("set PEXELS_API_KEY or PIXABAY_API_KEY (in the environment or a .env file)")


def pick(assets: Sequence[Asset], used: Sequence[str] = (),
         min_duration: float = 0.0) -> Tuple[Optional[Asset], List[str]]:
    """First usable asset: vertical, not already used, no brand words when avoidable.

    Returns the asset and notes on what was skipped.
    """
    notes: List[str] = []
    fresh = [a for a in assets if a.vertical and a.id not in used]
    if not fresh:
        return None, notes
    if min_duration:
        long_enough = [a for a in fresh if a.duration >= min_duration]
        fresh = long_enough or fresh
    for a in fresh:
        word = mentions_brand(a)
        if word is None:
            return a, notes
        notes.append(f"skipped {a.provider} {a.id} (mentions '{word}')")
    notes.append("every result mentions a brand; using the first one, check it for logos")
    return fresh[0], notes


@dataclass
class Picked:
    scene: int
    kind: str
    query: str
    file: str
    credit: dict
    notes: List[str] = field(default_factory=list)


def _download(asset: Asset, dest: Path, fetch: Fetch) -> None:
    data = fetch(asset.url, {})
    if not data:
        raise StockError(f"{asset.provider} {asset.id}: empty download")
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def fill_scenes(
    scenes_path: Path,
    provider: Provider,
    fetch: Fetch = http_fetch,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> List[Picked]:
    """Download an asset for every scene that has a query but no file yet.

    Updates scenes.json in place: ``image`` or ``video`` gets the file name
    and ``credit`` the source. With ``force``, scenes with a query are
    fetched again (skipping the asset they had).
    """
    scenes_path = Path(scenes_path)
    raw = json.loads(scenes_path.read_text(encoding="utf-8"))
    entries = raw.get("scenes") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise StockError(f"{scenes_path}: expected a list of scenes")
    folder = scenes_path.parent
    used = [str((e.get("credit") or {}).get("id")) for e in entries if isinstance(e, dict) and e.get("credit")]
    picked: List[Picked] = []
    for i, entry in enumerate(entries, start=1):
        kind = "video" if entry.get("video_query") else "image" if entry.get("image_query") else None
        if kind is None:
            continue
        current = entry.get(kind)
        if current and (folder / current).is_file() and not force:
            continue
        query = str(entry[f"{kind}_query"])
        found = provider.videos(query) if kind == "video" else provider.images(query)
        skip = list(used)
        old = (entry.get("credit") or {}).get("id")
        if force and old:
            skip.append(str(old))
        asset, notes = pick(found, skip, min_duration=float(entry.get("duration") or 0))
        if asset is None:
            raise StockError(f"scene {i}: no vertical {kind} for '{query}' on {provider.name}")
        ext = ".mp4" if kind == "video" else (Path(urllib.parse.urlsplit(asset.url).path).suffix.lower() or ".jpg")
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
            ext = ".jpg"
        name = f"{i:02d}{ext}"
        _download(asset, folder / name, fetch)
        size = f"{asset.width}x{asset.height}"
        if kind == "image":
            try:
                from PIL import Image

                with Image.open(folder / name) as im:
                    size = f"{im.width}x{im.height}"
            except OSError:
                raise StockError(f"scene {i}: {name} is not a readable image") from None
        entry.pop("image" if kind == "video" else "video", None)
        entry[kind] = name
        entry["credit"] = asset.credit()
        used.append(asset.id)
        picked.append(Picked(i, kind, query, name, asset.credit(), notes))
        for note in notes:
            log(f"  scene {i}: {note}")
        log(f"scene {i}: {kind} '{query}' -> {name} ({asset.provider} {asset.id} by {asset.author}, {size})")
    scenes_path.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return picked


def build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="wkvideo assets",
        description="Download a stock photo or vertical video for every scene with image_query or "
                    "video_query (Pexels when PEXELS_API_KEY is set, otherwise Pixabay).",
    )
    p.add_argument("folder", type=Path, help="video folder with scenes.json")
    p.add_argument("--scenes", type=Path, help="scenes file (default: FOLDER/scenes.json)")
    p.add_argument("--force", action="store_true", help="fetch again, picking a different asset")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE,
                   help=f"where search results are cached for 24h (default: {DEFAULT_CACHE})")
    p.add_argument("--env-file", type=Path, help="file with the API keys (default: the nearest .env)")
    return p


def main(argv=None, fetch: Fetch = http_fetch) -> int:
    import sys

    from .env import load_env

    args = build_parser().parse_args(argv)
    scenes = args.scenes or args.folder / "scenes.json"
    if not scenes.is_file():
        print(f"error: scenes file not found: {scenes}", file=sys.stderr)
        return 2
    load_env([args.folder], args.env_file)
    try:
        provider = choose_provider(SearchCache(args.cache_dir), fetch)
        print(f"Using {provider.name}")
        picked = fill_scenes(scenes, provider, fetch, force=args.force)
    except (StockError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{len(picked)} asset(s) downloaded, {provider.requests} search request(s) sent "
          f"(the rest came from the 24h cache)")
    return 0
