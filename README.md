# AI Auto Grader - Data Preparation and EDA Starter

This repo contains a practical EDA starter kit for the CS581 auto-grading project.

## Final Unified Pipeline (New)

Use the new modular runner in `scripts/cli/run_pipeline.py` for the merged architecture
(Sai + friend logic):

- `extract` mode: model-agnostic extraction only (PyMuPDF/Camelot/openpyxl/BeautifulSoup + OCR + captions).
- `describe` mode: run one model (`openai`, `gemini`, `anthropic`) on extracted artifacts.
- `full` mode: extract then describe in one command.
- `compare` mode: compare multiple `summary.json` files.
- `index` mode: index lecture chunks into ChromaDB.
- `retrieve` mode: retrieve lecture context for student chunks.
- `grade` mode: rubric-aware grading using retrieved lecture context.

Output structure:

```text
outputs/final_phase1/<run_id>/
├── extract/
│   ├── manifest.json
│   ├── images/
│   ├── tables/
│   ├── text_blocks/
│   ├── ocr_results/
│   └── per_file_json/
├── describe_<provider>_<model>/
│   ├── chunks.jsonl
│   ├── summary.json
│   └── per_file_json/
└── comparison/
    ├── comparison_report.md
    └── comparison_report.json
```

Quick commands:

```bash
# 1) Extract once (no model cost)
python scripts/cli/run_pipeline.py \
  --mode extract \
  --data-dir "Spring 2026" \
  --output-root "outputs/final_phase1" \
  --run-id "run_01"

# 2) Describe with OpenAI
python scripts/cli/run_pipeline.py \
  --mode describe \
  --extract-dir "outputs/final_phase1/run_01/extract" \
  --describe-dir "outputs/final_phase1/run_01/describe_openai_gpt-4o-2024-11-20" \
  --vision-provider openai \
  --vision-model "gpt-4o-2024-11-20" \
  --vision-input-cost-per-1m 5.0 \
  --vision-output-cost-per-1m 15.0

# 3) Index lecture chunks into Chroma
python scripts/cli/run_pipeline.py \
  --mode index \
  --chunks-jsonl "outputs/final_phase1/run_01/describe_openai_gpt-4o-2024-11-20/chunks.jsonl" \
  --chroma-path "outputs/final_phase1/run_01/chroma_db" \
  --chroma-collection "phase1_chunks"

# 4) Retrieve lecture context for student chunks
python scripts/cli/run_pipeline.py \
  --mode retrieve \
  --chunks-jsonl "outputs/final_phase1/run_01/describe_openai_gpt-4o-2024-11-20/chunks.jsonl" \
  --chroma-path "outputs/final_phase1/run_01/chroma_db" \
  --chroma-collection "phase1_chunks" \
  --retrieval-out-jsonl "outputs/final_phase1/run_01/retrieval_results.jsonl" \
  --retrieval-top-k 6

# 5) Grade one student (with rubric)
python scripts/cli/run_pipeline.py \
  --mode grade \
  --chunks-jsonl "outputs/final_phase1/run_01/describe_openai_gpt-4o-2024-11-20/chunks.jsonl" \
  --retrieval-out-jsonl "outputs/final_phase1/run_01/retrieval_results.jsonl" \
  --student-path "Student 1" \
  --rubric-file "docs/rubric.txt" \
  --grading-provider openai \
  --grading-model "gpt-4o-2024-11-20"
```

Web MVP demo (upload PDF -> model select -> grade):

```bash
streamlit run scripts/cli/mvp_web.py
```

Notes for web MVP:
- Upload 3 files in UI: `student submission (.pdf or .xlsx)` + optional `rubric` + optional `assignment instructions`.
- Rubric/assignment can be `.txt`, `.md`, `.pdf`, or `.docx`.
- RAG retrieval uses a run-specific Chroma DB and local embeddings by default to avoid OpenAI embedding permission errors.

Note: sections below this block are legacy starter notes from earlier iterations.
For the current merged architecture, follow the commands in this "Final Unified Pipeline (New)" section.

It is designed around the project scope from the Spring 2026 ETI/Spark collaboration:
- Text-based grading already works.
- Gaps are mostly with Excel, PDFs that include images, and image/diagram submissions.
- EDA should focus on data quality, structure, and extractability for AI + RAG workflows.

## What is included

- `scripts/run_eda.py`: runs multimodal EDA over a dataset folder.
- `scripts/tools/create_tool_comparison_matrix.py`: generates a structured matrix for comparing multiple AI tools on the same files.
- `scripts/evaluate_student_extraction.py`: evaluates extraction quality specifically for student `.pdf` and `.xlsx` submissions.
- `scripts/extract_workflow_diagrams.py`: extracts workflow diagrams (nodes + inferred edges + ordered steps) from PDF/image files.
- `scripts/legacy/phase1_multimodal_pipeline.py`: Phase 1 pipeline for PDF/XLSX extraction, caption matching, OCR/vision image processing, and vector-ready chunk output.
- `requirements.txt`: Python dependencies.
- `docs/notion_data_preparation_eda.md`: draft text to paste into your Notion "Data Preparation and EDA" page.
- `docs/virtual_meeting_walkthrough.md`: short screen-share walkthrough plan.
- `docs/tool_comparison_playbook.md`: scoring rubric + process for platform comparison.

## Quick start

1. Create and activate a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Put project files in a local folder (for example `data/`) and run EDA.

```bash
python scripts/run_eda.py --data-dir data --output-dir outputs/eda
```

Optional OCR pass for image files (requires local Tesseract binary installed):

```bash
python scripts/run_eda.py --data-dir data --output-dir outputs/eda --enable-ocr
```

4. Generate tool-comparison matrix for manual/assisted evaluation across platforms.

```bash
python scripts/tools/create_tool_comparison_matrix.py \
  --data-dir data \
  --output-dir outputs/tool_comparison \
  --tools "Azure AI Foundry,OpenAI,Gemini,Claude"
```

5. Evaluate student submission extraction quality (PDF + XLSX only).

```bash
python scripts/evaluate_student_extraction.py \
  --data-dir "Spring 2026" \
  --output-dir outputs/student_extraction \
  --student-pattern "(?i)student" \
  --save-text
```

Notes:
- By default, saved extraction text is **not truncated**.
- If you want short previews instead, add `--max-preview-chars 1200` (or any number).

6. Extract workflow diagrams as structured data (diagram-aware extraction).

```bash
python scripts/extract_workflow_diagrams.py \
  --input-path "Spring 2026/Assignment Examples Fall 2025 /Assignment 1_ Diagram & Text/Student 1 - Good Example/Student 1.pdf" \
  --output-dir outputs/workflow_diagrams \
  --max-pages-per-pdf 8
```

You can also pass a folder to process multiple student PDFs/images at once:

```bash
python scripts/extract_workflow_diagrams.py \
  --input-path "Spring 2026/Assignment Examples Fall 2025 /Assignment 1_ Diagram & Text" \
  --output-dir outputs/workflow_diagrams_students \
  --max-pages-per-pdf 8 \
  --top-k-per-file 1
```

7. Run the Phase 1 multimodal extraction pipeline (PDF + XLSX + images).

OpenAI GPT-4o image descriptions (strict Phase 1 behavior: all images -> `image_description`):

```bash
export OPENAI_API_KEY="<your_key>"
python scripts/legacy/phase1_multimodal_pipeline.py \
  --data-dir "Spring 2026" \
  --output-dir outputs/phase1_pipeline \
  --vision-provider openai \
  --vision-model "gpt-4o" \
  --image-handling vision_only \
  --vision-max-tokens 1800 \
  --vision-retry-max-tokens 2500 \
  --image-large-pixels-threshold 1000000 \
  --image-tile-target-max-pixels 1000000 \
  --image-max-tiles 9 \
  --vision-input-cost-per-1m 5.0 \
  --vision-output-cost-per-1m 15.0
```

Large-image behavior: if `width*height >= image-large-pixels-threshold`, the script tiles the image, calls vision per tile, merges outputs, retries once on invalid JSON, and records `tiled`/`tile_count` metadata.

The pipeline now supports `.pdf`, `.xlsx`, `.html`, and `.htm` input files.
HTML processing keeps only core text elements (`p`, `h1`, `h2`, `h3`, `li`) and strips scripts/styles/navigation containers.

Claude Sonnet image descriptions:

```bash
export ANTHROPIC_API_KEY="<your_key>"
python scripts/legacy/phase1_multimodal_pipeline.py \
  --data-dir "Spring 2026" \
  --output-dir outputs/phase1_pipeline_claude \
  --vision-provider anthropic \
  --vision-model "claude-3-5-sonnet-latest" \
  --image-handling vision_only
```

Optional legacy behavior (OCR for scanned pages, vision for others):

```bash
python scripts/legacy/phase1_multimodal_pipeline.py \
  --data-dir "Spring 2026" \
  --output-dir outputs/phase1_pipeline_mixed \
  --vision-provider openai \
  --vision-model "gpt-4o" \
  --image-handling ocr_then_vision
```

Optional direct Chroma ingestion:

```bash
python scripts/legacy/phase1_multimodal_pipeline.py \
  --data-dir "Spring 2026" \
  --output-dir outputs/phase1_pipeline_chroma \
  --vision-provider openai \
  --vision-model "gpt-4o" \
  --image-handling vision_only \
  --vector-db chroma \
  --chroma-path outputs/phase1_pipeline_chroma/chroma_db \
  --chroma-collection phase1_chunks
```

8. Export PDF extraction as readable DOCX (text plus image-extracted text in page order).

```bash
python scripts/exporters/export_pdf_extraction_docx.py \
  --per-file-json-dir outputs/phase1_pipeline/per_file_json \
  --output-dir outputs/phase1_pipeline/docx_reconstruction
```

9. Export Excel extraction as readable DOCX (sheet-wise tables and embedded-image text).

```bash
python scripts/exporters/export_excel_extraction_docx.py \
  --per-file-json-dir outputs/phase1_pipeline/per_file_json \
  --output-dir outputs/phase1_pipeline/excel_docx_reconstruction
```

10. Generate presentation visuals from Phase 1 results.

```bash
python scripts/visuals/generate_phase1_visuals.py \
  --summary-json outputs/phase1_pipeline/summary.json \
  --chunks-jsonl outputs/phase1_pipeline/chunks.jsonl \
  --output-dir outputs/phase1_pipeline/visuals
```

## Outputs

The script writes:
- `outputs/eda/summary.json`
- `outputs/eda/file_profiles.csv`
- `outputs/eda/eda_report.md`
- `outputs/eda/charts/*` (if `matplotlib` is installed)

Use `eda_report.md` as your discussion artifact during the virtual meeting, and copy/edit the Notion draft from `docs/notion_data_preparation_eda.md`.

For platform/tool comparison, use:
- `outputs/tool_comparison/tool_comparison_matrix.csv`
- `outputs/tool_comparison/tool_comparison_runbook.md`
- `docs/tool_comparison_playbook.md`

For student extraction quality, use:
- `outputs/student_extraction/extraction_summary.csv`
- `outputs/student_extraction/extraction_report.md`
- `outputs/student_extraction/extracted_text/*` (per-file extracted text)

For workflow diagram extraction, use:
- `outputs/workflow_diagrams/workflow_manifest.json`
- `outputs/workflow_diagrams/*_workflow.json`
- `outputs/workflow_diagrams/*_annotated.png`

For Phase 1 multimodal pipeline, use:
- `outputs/phase1_pipeline/chunks.jsonl`
- `outputs/phase1_pipeline/summary.json`
- `outputs/phase1_pipeline/extracted_images/*`
- `outputs/phase1_pipeline/per_file_json/*` (one JSON per source PDF/XLSX, mirrored folder structure)
- `outputs/phase1_pipeline/docx_reconstruction/*` (PDF reconstructions for qualitative extraction review)
- `outputs/phase1_pipeline/excel_docx_reconstruction/*` (Excel reconstructions for qualitative extraction review)
- `outputs/phase1_pipeline/visuals/*` (slide-ready charts and extraction comparison image)
