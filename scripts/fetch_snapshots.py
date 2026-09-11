#!/usr/bin/env python3
"""
Fetch a fixed list of Apple App Store and Google Play legal/policy pages,
extract their main readable text (stripping nav/footer/script
boilerplate), and write each one to a stable file path under snapshots/.

This script is intentionally "dumb": it does no summarization or AI
processing. It just gets a clean, comparable text snapshot of each page
so that `git diff` between runs is meaningful. Any actual summarizing of
*changes* happens later, in diff_and_notify.py, only on the diff itself.
"""
import io
import sys
import time

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

# name -> (url, output file, kind)
# kind is one of:
#   "html" (default) - regular Apple web page, extracted like before
#   "pdf"             - PDF document at a fixed URL, text pulled out page
#                       by page
#   "pdf-from-listing" - the PDF's own URL isn't stable (Apple embeds a
#                       revision date in the filename), so `url` here
#                       points at the *listing* page instead; the actual
#                       PDF link is found by regex on that page (see
#                       resolve_pdf_from_listing) before being fetched
#   "docc-json"       - developer.apple.com DocC/Vue pages (e.g. Human
#                       Interface Guidelines) render their content
#                       client-side; the actual copy lives in a JSON
#                       endpoint at /tutorials/data/<path>.json instead of
#                       the server-rendered HTML
#   "text"            - already-plain-text response (e.g. Google's
#                       ".md.txt" doc endpoints), written out as-is
#                       (whitespace-normalized) with no HTML/PDF parsing
#
# Note: the Apple Developer Program License Agreement is also linked from
# https://developer.apple.com/programs/apple-developer-program-license-agreement/
# but that URL 301-redirects to the "support/terms" URL already tracked
# below, so it isn't a separate document and doesn't need its own entry.
#
# Note: the App Store Connect Terms of Service
# (https://appstoreconnect.apple.com/WebObjects/iTunesConnect.woa/wa/termsOfService/)
# sits behind Apple ID login (it redirects to an auth page for anonymous
# requests), so it can't be fetched by this unauthenticated script. It's
# intentionally not tracked here; check it manually if needed.
DOCUMENTS = {
    "developer-program-license-agreement": (
        "https://developer.apple.com/support/terms/apple-developer-program-license-agreement/",
        "snapshots/developer-program-license-agreement.md",
        "html",
    ),
    "testflight-terms": (
        "https://www.apple.com/legal/internet-services/itunes/testflight/",
        "snapshots/testflight-terms.md",
        "html",
    ),
    "app-store-review-guidelines": (
        "https://developer.apple.com/app-store/review/guidelines/",
        "snapshots/app-store-review-guidelines.md",
        "html",
    ),
    "human-interface-guidelines": (
        "https://developer.apple.com/tutorials/data/design/human-interface-guidelines.json",
        "snapshots/human-interface-guidelines.md",
        "docc-json",
    ),
    "sign-in-with-apple-guidelines": (
        "https://developer.apple.com/sign-in-with-apple/usage-guidelines-for-websites-and-other-platforms/",
        "snapshots/sign-in-with-apple-guidelines.md",
        "html",
    ),
    "xcode-sla": (
        "https://www.apple.com/legal/sla/docs/xcode.pdf",
        "snapshots/xcode-sla.md",
        "pdf",
    ),
    "developer-agreement-pdf": (
        # Not the PDF itself - the page listing it. The real filename
        # (e.g. Apple-Developer-Agreement-20250318-English.pdf) embeds a
        # revision date that changes whenever Apple updates the
        # agreement, so it's re-discovered on every run instead of
        # hardcoded.
        "https://developer.apple.com/support/downloads/terms/apple-developer-agreement/",
        "snapshots/developer-agreement-pdf.md",
        "pdf-from-listing",
    ),
    "google-play-developer-terms": (
        "https://developers.google.com/profile/terms.md.txt",
        "snapshots/google-play-developer-terms.md",
        "text",
    ),
    "google-play-content-policy": (
        "https://developers.google.com/profile/content-policy.md.txt",
        "snapshots/google-play-content-policy.md",
        "text",
    ),
    "google-play-developer-distribution-agreement": (
        # play.google/developer-distribution-agreement.html serves
        # whatever language matches the requester's geo-IP (German for
        # a German-hosted CI runner, say), which would make every daily
        # diff pure translation noise. The /intl/en_us/ path pins it to
        # English regardless of where the fetch runs from.
        "https://play.google/intl/en_us/developer-distribution-agreement.html",
        "snapshots/google-play-developer-distribution-agreement.md",
        "html",
    ),
}

# Matches the English-language Developer Agreement PDF link on the
# listing page, e.g. ".../Apple-Developer-Agreement-20250318-English.pdf"
# (the page also links Chinese/German/Japanese/... variants of the same
# filename pattern, which this intentionally excludes).
DEVELOPER_AGREEMENT_PDF_RE = re.compile(
    r'href="([^"]*Apple-Developer-Agreement-[0-9]+-English\.pdf)"'
)

HEADERS = {
    # A normal browser UA avoids some basic bot-blocking; Apple's pages are
    # public and this is a plain GET, same as any browser would do.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def fetch(url: str, retries: int = 3, backoff: float = 5.0) -> requests.Response:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  attempt {attempt}/{retries} failed: {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def extract_text(html: str, url: str) -> str:
    """Strip an Apple legal/docs page down to its readable body text.

    Deliberately simple (BeautifulSoup + tag stripping, no ML-based
    boilerplate detection) so behavior is predictable and testable: same
    HTML in -> same text out, every time, which is what makes `git diff`
    across weekly runs trustworthy.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        tag.decompose()
    # Nav/header/footer/aside are boilerplate on these pages (site nav,
    # cookie banners, breadcrumbs, footer links) - not part of the legal
    # text itself.
    for tag in soup.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()
    # Common non-content regions on developer.apple.com / apple.com pages.
    for selector in [
        {"id": "ac-globalnav"},
        {"id": "ac-localnav"},
        {"class": "globalnav"},
        {"class": "localnav"},
        {"class": "footer"},
        {"class": re.compile(r"cookie", re.I)},
    ]:
        for tag in soup.find_all(attrs=selector):
            tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n", strip=True)

    # Collapse runs of blank lines so trivial whitespace churn doesn't
    # show up as a "change" week over week.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def extract_plain_text(raw: str) -> str:
    """Normalize an already-plain-text response (no HTML/PDF to strip)."""
    text = re.sub(r"\n{3,}", "\n\n", raw)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def resolve_pdf_from_listing(listing_url: str) -> str:
    """Find the English Developer Agreement PDF's current URL.

    The PDF filename embeds a revision date
    (Apple-Developer-Agreement-<date>-English.pdf) that changes whenever
    Apple publishes a new version, so instead of hardcoding it, scrape the
    listing page for whichever dated filename is live right now.
    """
    listing_html = fetch(listing_url).text
    match = DEVELOPER_AGREEMENT_PDF_RE.search(listing_html)
    if not match:
        raise RuntimeError(
            f"couldn't find an 'Apple-Developer-Agreement-*-English.pdf' "
            f"link on {listing_url} - Apple may have changed the page/naming"
        )
    return urljoin(listing_url, match.group(1))


def extract_pdf_text(content: bytes) -> str:
    """Pull plain text out of a PDF, page by page."""
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(p.strip() for p in pages)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def _walk_docc_text(node, out: list) -> None:
    """Recursively pull readable strings out of a DocC render-JSON tree.

    developer.apple.com pages like the Human Interface Guidelines are a
    client-rendered Vue app; the actual copy lives in a JSON blob (see the
    "docc-json" DOCUMENTS entries) shaped as deeply nested
    sections/paragraphs/inline-runs/topic-references rather than HTML.
    Rather than modeling that whole schema, just walk the tree and collect
    every leaf "text" and "title" value in document order - simple, and
    resilient to Apple tweaking the JSON structure.
    """
    if isinstance(node, dict):
        for key in ("title", "text"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                out.append(value)
        for value in node.values():
            _walk_docc_text(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_docc_text(item, out)


def extract_docc_json_text(data: dict) -> str:
    pieces: list = []
    for key in ("abstract", "primaryContentSections", "topicSections", "references"):
        if key in data:
            _walk_docc_text(data[key], pieces)
    text = "\n".join(pieces)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def main() -> int:
    ok = True
    for name, (url, path, kind) in DOCUMENTS.items():
        print(f"Fetching {name}: {url}")
        try:
            if kind == "pdf-from-listing":
                pdf_url = resolve_pdf_from_listing(url)
                print(f"  resolved PDF: {pdf_url}")
                resp = fetch(pdf_url)
                text = extract_pdf_text(resp.content)
                source_url = pdf_url
            elif kind == "pdf":
                resp = fetch(url)
                text = extract_pdf_text(resp.content)
                source_url = url
            elif kind == "docc-json":
                resp = fetch(url)
                text = extract_docc_json_text(resp.json())
                source_url = url
            elif kind == "text":
                resp = fetch(url)
                text = extract_plain_text(resp.text)
                source_url = url
            else:
                resp = fetch(url)
                text = extract_text(resp.text, url)
                source_url = url
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {e}", file=sys.stderr)
            ok = False
            continue

        if len(text) < 500:
            print(
                f"  WARNING: extracted text for {name} looks suspiciously "
                f"short ({len(text)} chars) - not overwriting snapshot, "
                f"needs a human look at the page/extractor.",
                file=sys.stderr,
            )
            ok = False
            continue

        # IMPORTANT: do not embed a fetch timestamp in the file itself -
        # that would make every single run "change" the file and defeat
        # the whole point of diffing. The commit date already records
        # when each version was captured; keep only the stable source URL.
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"<!-- source: {source_url} -->\n\n")
            f.write(text)
        print(f"  wrote {path} ({len(text)} chars)")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
