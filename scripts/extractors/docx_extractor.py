from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chunking import clean_text, make_sort_key
from extractors.html_extractor import (
    ExtractedHTML,
    HtmlTextBlock,
    extracted_html_to_jsonable,
)


def extract_docx(file_path: Path, rel_path: str, cfg: dict[str, Any]) -> ExtractedHTML:
    from docx import Document  # type: ignore
    from docx.oxml.ns import qn  # type: ignore

    doc = Document(str(file_path))
    min_chars = int(cfg.get("min_text_chars", 30))
    out: list[HtmlTextBlock] = []
    stats = {"paragraphs_scanned": 0, "tables_scanned": 0, "text_blocks": 0}
    doc_order = 0
    block_idx = 0

    for block in doc.element.body:
        tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag

        if tag == "p":
            stats["paragraphs_scanned"] += 1
            text = clean_text(
                "".join(r.text or "" for r in block.findall(f".//{qn('w:t')}"))
            )
            if len(text) >= min_chars:
                block_idx += 1
                doc_order += 1
                out.append(
                    HtmlTextBlock(
                        element_tag="p",
                        page_number=1,
                        block_index=block_idx,
                        text=text,
                        sort_key=make_sort_key(1, block_idx),
                        document_order=doc_order,
                    )
                )

        elif tag == "tbl":
            stats["tables_scanned"] += 1
            for tr in block.findall(f".//{qn('w:tr')}"):
                cells = [
                    "".join(t.text or "" for t in tc.findall(f".//{qn('w:t')}")).strip()
                    for tc in tr.findall(f".//{qn('w:tc')}")
                ]
                row_text = clean_text(" | ".join(c for c in cells if c))
                if len(row_text) >= min_chars:
                    block_idx += 1
                    doc_order += 1
                    out.append(
                        HtmlTextBlock(
                            element_tag="p",
                            page_number=1,
                            block_index=block_idx,
                            text=row_text,
                            sort_key=make_sort_key(1, block_idx),
                            document_order=doc_order,
                        )
                    )

    stats["text_blocks"] = len(out)
    return ExtractedHTML(source_path=rel_path, file_type="docx", text_blocks=out, stats=stats)


extracted_docx_to_jsonable = extracted_html_to_jsonable
