"""HTML scraper for Microsoft Learn unit pages."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .models import ContentBlock, ContentBlockType, Module, Unit

log = logging.getLogger(__name__)

DELAY_BETWEEN_REQUESTS = 1.0
USER_AGENT = "MSLearnAgent/1.0 (study-tool)"


class MSLearnScraper:
    def __init__(self, output_dir: Path, locale: str = "en-us"):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.output_dir = output_dir
        self.image_dir = output_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.locale = locale
        self._last_request_time = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < DELAY_BETWEEN_REQUESTS:
            time.sleep(DELAY_BETWEEN_REQUESTS - elapsed)
        self._last_request_time = time.time()

    def _fetch(self, url: str) -> str:
        self._rate_limit()
        log.info("Fetching %s", url)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    # ── Module page: extract unit URLs from TOC ──

    def populate_unit_urls(self, module: Module) -> Module:
        """Scrape the module landing page to fill in unit URLs."""
        module_url = module.url.rstrip("/")
        html = self._fetch(module_url)
        soup = BeautifulSoup(html, "html.parser")

        # Find unit links in the module page — they appear as relative links
        # in the module's unit list/TOC area
        unit_links: list[tuple[str, str]] = []

        # Strategy 1: look for links that match unit slug patterns (N-slug)
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            # Unit links are relative like "1-introduction" or "./1-introduction"
            if re.match(r"\.?/?(\d+-[\w-]+)", href):
                clean = re.match(r"\.?/?(\d+-[\w-]+)", href).group(1)
                full_url = f"{module_url}/{clean}"
                title = a_tag.get_text(strip=True)
                unit_links.append((title, full_url))

        if unit_links:
            # Match discovered URLs to units by position
            for i, unit in enumerate(module.units):
                if i < len(unit_links):
                    unit.url = unit_links[i][1]
                    if not unit.title:
                        unit.title = unit_links[i][0]
        else:
            # Fallback: construct URLs from unit UIDs
            log.warning("Could not find unit links in module page, using UID fallback")
            for i, unit in enumerate(module.units):
                # UID like: learn.wwl.module-name.unit-slug
                uid_parts = unit.uid.split(".")
                slug = uid_parts[-1] if uid_parts else unit.uid
                # Add positional prefix
                unit.url = f"{module_url}/{i + 1}-{slug}"

        return module

    # ── Unit page: extract content ──

    def fetch_unit_content(self, unit: Unit, module_url: str) -> Unit:
        """Fetch and parse a unit page into ContentBlocks."""
        if not unit.url:
            log.warning("No URL for unit %s, skipping", unit.uid)
            return unit

        try:
            html = self._fetch(unit.url)
        except requests.RequestException as e:
            log.error("Failed to fetch unit %s: %s", unit.url, e)
            return unit

        soup = BeautifulSoup(html, "html.parser")

        # Check if this is a knowledge check
        meta_completion = soup.find("meta", attrs={"name": "unit_completion_type"})
        if meta_completion and meta_completion.get("content") == "quiz":
            unit.is_knowledge_check = True

        # Find main content area
        content_el = self._find_content_area(soup)
        if not content_el:
            log.warning("Could not find content area for %s", unit.url)
            return unit

        unit.content_blocks = self._parse_content(content_el, module_url)
        return unit

    def _find_content_area(self, soup: BeautifulSoup) -> Tag | None:
        """Find the main content container on the page."""
        # Try common MS Learn content selectors
        for selector in [
            "div.unit-inner-section",
            "div[id='unit-inner-section']",
            "main .content",
            "div.content",
            "article",
            "main",
        ]:
            el = soup.select_one(selector)
            if el:
                return el
        return soup.find("body")

    def _parse_content(self, container: Tag, module_url: str) -> list[ContentBlock]:
        """Parse HTML elements into ContentBlocks."""
        blocks: list[ContentBlock] = []
        module_base = module_url.rstrip("/")

        for el in container.children:
            if not isinstance(el, Tag):
                continue

            # Skip navigation, metadata, and script elements
            if el.name in ("script", "style", "nav", "header", "footer", "meta"):
                continue

            # Headings
            if el.name in ("h1", "h2", "h3", "h4"):
                text = el.get_text(strip=True)
                if text and text not in ("Completed", "Next unit"):
                    level = int(el.name[1])
                    blocks.append(ContentBlock(
                        block_type=ContentBlockType.HEADING,
                        text=text,
                        level=level,
                    ))

            # Paragraphs
            elif el.name == "p":
                # Check if paragraph contains only an image
                img = el.find("img")
                if img:
                    block = self._parse_image(img, module_base)
                    if block:
                        blocks.append(block)
                text = el.get_text(strip=True)
                if text:
                    blocks.append(ContentBlock(
                        block_type=ContentBlockType.PARAGRAPH,
                        text=text,
                    ))

            # Lists
            elif el.name in ("ul", "ol"):
                items = [li.get_text(strip=True) for li in el.find_all("li", recursive=False)]
                # Filter out nav items (e.g., "Completed", "6 minutes")
                nav_words = {"Completed", "Next unit", "Previous unit"}
                items = [i for i in items if i and i not in nav_words
                         and not re.match(r"^\d+\s*minutes?$", i)]
                if items:
                    blocks.append(ContentBlock(
                        block_type=ContentBlockType.LIST,
                        list_items=items,
                        ordered=(el.name == "ol"),
                    ))

            # Tables
            elif el.name == "table":
                block = self._parse_table(el)
                if block:
                    blocks.append(block)

            # Images (standalone, not inside <p>)
            elif el.name == "img":
                block = self._parse_image(el, module_base)
                if block:
                    blocks.append(block)

            # Divs — could be callout boxes, nested content, or images
            elif el.name == "div":
                blocks.extend(self._parse_div(el, module_base))

            # Pre/code blocks
            elif el.name == "pre":
                code = el.get_text()
                if code.strip():
                    blocks.append(ContentBlock(
                        block_type=ContentBlockType.CODE,
                        text=code.strip(),
                    ))

        return blocks

    def _parse_image(self, img: Tag, module_base: str) -> ContentBlock | None:
        """Parse an img tag into an IMAGE ContentBlock and download it."""
        src = img.get("src", "")
        alt = img.get("alt", "")
        if not src:
            return None

        # Skip SVG badges and icons
        if src.endswith(".svg") or "badge" in src.lower() or "achievement" in src.lower():
            return None

        # Resolve relative URL
        if src.startswith("media/"):
            full_url = f"{module_base}/{src}"
        elif src.startswith("./media/"):
            full_url = f"{module_base}/{src[2:]}"
        elif src.startswith("http"):
            full_url = src
        else:
            full_url = urljoin(module_base + "/", src)

        # Download image
        local_path = self._download_image(full_url)

        return ContentBlock(
            block_type=ContentBlockType.IMAGE,
            image_url=full_url,
            image_alt=alt,
            image_path=local_path,
        )

    def _download_image(self, url: str) -> Path | None:
        """Download an image to the local images directory."""
        try:
            # Extract filename from URL
            filename = url.split("/")[-1].split("?")[0]
            if not filename:
                return None

            local_path = self.image_dir / filename
            if local_path.exists():
                return local_path

            self._rate_limit()
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            log.info("Downloaded image: %s", filename)
            return local_path
        except Exception as e:
            log.warning("Failed to download image %s: %s", url, e)
            return None

    def _parse_table(self, table: Tag) -> ContentBlock | None:
        """Parse an HTML table into a TABLE ContentBlock."""
        headers = []
        rows = []

        thead = table.find("thead")
        if thead:
            for th in thead.find_all(["th", "td"]):
                headers.append(th.get_text(strip=True))

        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                # If no thead, use first row as headers
                if not headers and not rows:
                    headers = cells
                else:
                    rows.append(cells)

        if not headers and not rows:
            return None

        return ContentBlock(
            block_type=ContentBlockType.TABLE,
            table_headers=headers,
            table_rows=rows,
        )

    def _parse_div(self, div: Tag, module_base: str) -> list[ContentBlock]:
        """Parse a div — handles callout boxes and nested content."""
        blocks: list[ContentBlock] = []

        # Check for alert/note/tip/warning callout boxes
        classes = " ".join(div.get("class", []))
        note_type = ""
        if "alert" in classes or "NOTE" in classes:
            note_type = "note"
        elif "TIP" in classes:
            note_type = "tip"
        elif "WARNING" in classes:
            note_type = "warning"
        elif "IMPORTANT" in classes:
            note_type = "important"

        if note_type:
            text = div.get_text(strip=True)
            # Remove the note type prefix if present (e.g., "Note" at the start)
            text = re.sub(r"^(Note|Tip|Warning|Important)\s*", "", text)
            if text:
                blocks.append(ContentBlock(
                    block_type=ContentBlockType.NOTE,
                    text=text,
                    note_type=note_type,
                ))
            return blocks

        # Otherwise, recurse into the div's children
        blocks.extend(self._parse_content(div, module_base))
        return blocks
