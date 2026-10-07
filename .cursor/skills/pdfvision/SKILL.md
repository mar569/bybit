---
name: pdfvision
description: >-
  Читает PDF: текст, раскладка, OCR, PNG страниц через CLI pdfvision и pymupdf4llm.
  Используй при локальном .pdf, URL PDF, сканах, слайдах, таблицах в PDF.
---

# PDF: pdfvision + PyMuPDF

Уже установлено локально (Windows):

- CLI: `pdfvision` (v0.18.0)
- Python: `pymupdf` + `pymupdf4llm` 1.28.2

## Порядок

1. Незнакомый документ: `pdfvision "path.pdf" --map`
2. Текст: `pdfvision "path.pdf"` или `-p 1-5`
3. Скан / картинки: `pdfvision "path.pdf" -p 1 --render --render-output ".cursor/pdf_pages"` затем смотри PNG
4. OCR: `--ocr` / `--ocr-lang rus+eng`
5. Markdown: `python -c "import pymupdf4llm; print(pymupdf4llm.to_markdown(r'path.pdf'))"`

Не опирайся на текст, если coverage 0% или битые глифы — сначала `--render`.
