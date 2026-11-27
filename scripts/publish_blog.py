#!/usr/bin/env python3
"""Publish the blog written in the dashboard (Django admin → Blog posts).

Reads https://ammar-academy.com/api/blog/export/ and updates, inside web/:
  - blog/index.html and blog/<slug>/index.html for every published article
    (an article that is no longer published loses its page),
  - the /blog/ entries of sitemap.xml,
  - the "## Blog (Arabic)" list in llms.txt (lines already there are kept).

Run by .github/workflows/publish-blog.yml. Prints what changed; changes
nothing if the dashboard has no published article or the answer looks wrong.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

API = os.environ.get("BLOG_EXPORT_URL", "https://ammar-academy.com/api/blog/export/")
SITE = "https://ammar-academy.com"
WEB = Path(__file__).resolve().parent.parent / "web"
FILE_RE = re.compile(r"^blog/(?:[a-z0-9]+(?:-[a-z0-9]+)*/)?index\.html$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def fetch() -> dict:
    last = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(API, headers={"User-Agent": "AA-frontend publish-blog"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception as error:  # noqa: BLE001
            last = error
            time.sleep(5 * (attempt + 1))
    fail(f"Could not read {API}: {last}")
    return {}


def check(data: dict) -> None:
    posts, files = data.get("posts"), data.get("files")
    if not isinstance(posts, list) or not isinstance(files, dict):
        fail("Unexpected answer from the API.")
    if not posts:
        fail("The dashboard has no published article; leaving the blog as it is.")
    for post in posts:
        if not SLUG_RE.match(post.get("slug", "")):
            fail(f"Bad slug: {post.get('slug')!r}")
        if f"blog/{post['slug']}/index.html" not in files:
            fail(f"No page for {post['slug']}")
    for name, html in files.items():
        if not FILE_RE.match(name) or not html.startswith("<!DOCTYPE html>"):
            fail(f"Unexpected file {name!r}")
    if "blog/index.html" not in files:
        fail("No blog/index.html")


def write_pages(data: dict) -> list[str]:
    changes = []
    for name, html in data["files"].items():
        path = WEB / name
        if not path.exists() or path.read_text(encoding="utf-8") != html:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            changes.append(f"wrote web/{name}")
    published = {p["slug"] for p in data["posts"]}
    for folder in sorted((WEB / "blog").iterdir()):
        if folder.is_dir() and SLUG_RE.match(folder.name) and folder.name not in published:
            if [f.name for f in folder.iterdir()] == ["index.html"]:
                shutil.rmtree(folder)
                changes.append(f"removed web/blog/{folder.name}/ (not published any more)")
    return changes


def update_sitemap(posts: list[dict]) -> list[str]:
    path = WEB / "sitemap.xml"
    text = path.read_text(encoding="utf-8")
    block = re.compile(r"  <url>\n    <loc>" + re.escape(SITE) + r"/blog/[^<]*</loc>\n(?:    <[^\n]*\n)*?  </url>\n")
    kept = block.sub("", text)
    entries = [(f"{SITE}/blog/", max(p["updated_on"] for p in posts))]
    entries += [(p["url"], p["updated_on"]) for p in posts]
    new = "".join(f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{day}</lastmod>\n  </url>\n" for loc, day in entries)
    updated = kept.replace("</urlset>", new + "</urlset>")
    if updated == text:
        return []
    path.write_text(updated, encoding="utf-8")
    return ["updated web/sitemap.xml"]


def update_llms(posts: list[dict]) -> list[str]:
    path = WEB / "llms.txt"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(## Blog \(Arabic\)\n\n)((?:- .*\n)+)", text)
    if not match:
        return []
    lines = match.group(2).splitlines()
    index_line = [line for line in lines if f"({SITE}/blog/)" in line][:1]
    by_url = {}
    for line in lines:
        found = re.match(r"- \[[^\]]*\]\(([^)]+)\)", line)
        if found:
            by_url[found.group(1)] = line
    new_lines = index_line + [by_url.get(p["url"], f"- [{p['headline']}]({p['url']}): {p['description']}") for p in posts]
    block = "\n".join(new_lines) + "\n"
    if block == match.group(2):
        return []
    updated = text[: match.start(2)] + block + text[match.end(2) :]
    updated = re.sub(r"Last updated: \d{4}-\d{2}-\d{2}\.", f"Last updated: {date.today().isoformat()}.", updated, count=1)
    path.write_text(updated, encoding="utf-8")
    return ["updated web/llms.txt"]


def main() -> None:
    data = fetch()
    check(data)
    posts = sorted(data["posts"], key=lambda p: (p["published_on"], p["slug"]))
    changes = write_pages(data) + update_sitemap(posts) + update_llms(posts)
    print("\n".join(changes) if changes else "The blog is up to date.")


if __name__ == "__main__":
    main()
