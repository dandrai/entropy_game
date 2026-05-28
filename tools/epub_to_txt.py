"""
Convert an EPUB file to a clean plain-text file suitable for corpus loading.

Usage:
    python tools/epub_to_txt.py path/to/book.epub data/fr/output.txt

Dependencies:
    pip install ebooklib beautifulsoup4

What it does:
    - Extracts all prose content from the EPUB's HTML/XHTML documents
    - Strips tags, footnotes, page numbers, and excessive whitespace
    - Writes UTF-8 plain text ready for corpus.py to load

After conversion, open the .txt file and note the character offset where the
actual prose begins (after any table of contents or prefatory matter), then
set start_offset in config.yaml accordingly.
"""

import re
import sys
from pathlib import Path


def epub_to_txt(epub_path: str, out_path: str) -> None:
    try:
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit(
            "Missing dependencies.\n"
            "Run:  pip install ebooklib beautifulsoup4"
        )

    book = epub.read_epub(epub_path)

    chunks = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")

        # Drop navigation, notes, and TOC sections
        for tag in soup.find_all(["nav", "aside"]):
            tag.decompose()
        for tag in soup.find_all(True, {"class": re.compile(r"note|footnote|toc|index", re.I)}):
            tag.decompose()
        for tag in soup.find_all(True, {"epub:type": re.compile(r"note|footnote|toc")}):
            tag.decompose()

        text = soup.get_text(separator="\n")
        chunks.append(text)

    raw = "\n\n".join(chunks)

    # Collapse runs of blank lines to at most two
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    # Remove page-number lines: lone integers or "— 12 —" style
    raw = re.sub(r"^\s*[—–-]?\s*\d+\s*[—–-]?\s*$", "", raw, flags=re.MULTILINE)
    # Strip trailing whitespace per line
    raw = "\n".join(line.rstrip() for line in raw.splitlines())
    raw = raw.strip() + "\n"

    Path(out_path).write_text(raw, encoding="utf-8")
    size_kb = Path(out_path).stat().st_size // 1024
    print(f"Written: {out_path}  ({size_kb} KB)")
    print()
    print("First 300 chars of output:")
    print(repr(raw[:300]))
    print()
    print("Next step: open the file, find where prose begins,")
    print("and set start_offset in config.yaml to that character position.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: python {sys.argv[0]} input.epub output.txt")
    epub_to_txt(sys.argv[1], sys.argv[2])
