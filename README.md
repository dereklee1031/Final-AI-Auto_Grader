# AI Auto Grader

An AI-powered grading assistant that reads student submissions (PDF, PPTX, XLSX), retrieves relevant context from course lecture materials, and produces rubric-aware scores and feedback.

---

## Requirements

- Python 3.10 or higher
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed on your system
  - macOS: `brew install tesseract`
  - Ubuntu/Debian: `sudo apt install tesseract-ocr`
  - Windows: download the installer from the Tesseract GitHub releases page
- At least one AI provider API key (see [Configuration](#configuration))

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd Final-AI-Auto_Grader

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt
pip install streamlit             # only needed if using the Streamlit UI
```

---

## Configuration

Create a `.env` file in the project root by copying the template below.
You only need keys for the providers you plan to use.

```bash
# .env

# At least one of the following is required:
ANTHROPIC_API_KEY=sk-ant-...     # Claude (Anthropic)
OPENAI_API_KEY=sk-proj-...       # GPT-4o (OpenAI)
GEMINI_API_KEY=AIzaSy...         # Gemini (Google)

# Embedding provider for lecture indexing (optional — auto-detected from keys above)
# Options: google | openai | default  (default = local embeddings, no API cost)
CHROMA_EMBEDDING_PROVIDER=google
```

> **Tip:** If you have a Gemini key, set `CHROMA_EMBEDDING_PROVIDER=google`. This gives higher-quality lecture retrieval at no extra cost. If you have no preference, omit the line and local embeddings will be used automatically.

---

## Step 1 — Index Lectures (one-time setup)

Before grading, the system needs to index the course lecture materials into a searchable vector database. This step only needs to be run once (or whenever lecture content changes).

A pre-built lecture chunk file is already included at:

```
outputs/final_phase1/lecture_chunks_hybrid.jsonl
```

Run the indexing command:

```bash
python scripts/cli/run_pipeline.py \
  --mode index \
  --chunks-jsonl "outputs/final_phase1/lecture_chunks_hybrid.jsonl" \
  --chroma-path "outputs/final_phase1/shared_lecture_chroma_google" \
  --chroma-collection "lecture_chunks"
```

> If you set `CHROMA_EMBEDDING_PROVIDER=openai` or `default`, replace `shared_lecture_chroma_google` in the path with `shared_lecture_chroma_openai` or `shared_lecture_chroma_default` respectively.

You will see log output as chunks are embedded and stored. This takes a few minutes on first run. The resulting index is saved to disk and reused automatically on every subsequent grading run — you do not need to re-index unless lecture materials change.

### Re-indexing after updating lectures

If you add or update lecture files, re-run the full extract + describe + index pipeline:

```bash
# Extract lecture content
python scripts/cli/run_pipeline.py \
  --mode extract \
  --data-dir "data/Spring 2026/Lecture [PDF versions]" \
  --output-root "outputs/final_phase1" \
  --run-id "lectures_reindex"

# Describe with your chosen vision model (e.g. Gemini)
python scripts/cli/run_pipeline.py \
  --mode describe \
  --extract-dir "outputs/final_phase1/lectures_reindex/extract" \
  --describe-dir "outputs/final_phase1/lectures_reindex/describe_gemini" \
  --vision-provider gemini

# Index the new chunks
python scripts/cli/run_pipeline.py \
  --mode index \
  --chunks-jsonl "outputs/final_phase1/lectures_reindex/describe_gemini/chunks.jsonl" \
  --chroma-path "outputs/final_phase1/shared_lecture_chroma_google" \
  --chroma-collection "lecture_chunks"
```

---

## Step 2 — Run the Grading UI

Two UI options are available. The **Flask web app** is recommended for regular use.

### Option A: Flask Web App (recommended)

```bash
python scripts/web/app.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser.

**What you can do in the UI:**

1. Upload a student submission (PDF, PPTX, or XLSX)
2. Optionally attach an assignment description and/or rubric (DOCX, PDF, TXT, or MD)
3. Select the AI provider and model (OpenAI, Gemini, or Anthropic)
4. Click **Grade** — the system will extract the submission, retrieve relevant lecture context, and produce a score with criterion-level feedback
5. Download results as JSON or CSV

The UI also includes a **library** where you can save rubrics and assignment instructions once so you don't need to re-upload them each session.

### Option B: Streamlit MVP (quick demo)

```bash
streamlit run scripts/cli/mvp_web.py
```

Streamlit will print a local URL (typically [http://localhost:8501](http://localhost:8501)). This interface is simpler and best suited for quick one-off grading or demonstrations.

---

## Data Layout

The `data/` folder is shared via Google Drive. Download it and place it in the project root so the structure looks like this:

```
data/
├── Spring 2026/
│   ├── Lecture [PDF versions]/      # Lecture PDFs for indexing
│   ├── Lectures [html versions]/    # Optional HTML lecture versions
│   └── Assignment Rubrics/          # Default rubric location
├── library/
│   ├── assignments/                 # Persistent assignment library (Flask UI)
│   └── rubrics/                     # Persistent rubric library (Flask UI)
```

Grading outputs are written to:

```
outputs/final_phase1/<run_id>/
├── extract/                         # Raw extracted text, images, tables
├── describe_<provider>_<model>/     # AI-generated chunk descriptions
│   └── chunks.jsonl
├── retrieval.jsonl                  # Student chunks + matched lecture context
└── grading/
    └── grades.json                  # Final scores and feedback
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `tesseract: command not found` | Install Tesseract (see [Requirements](#requirements)) |
| `chromadb` embedding errors | Set `CHROMA_EMBEDDING_PROVIDER=default` in `.env` to use local embeddings |
| `ModuleNotFoundError: streamlit` | Run `pip install streamlit` |
| Flask app starts but shows no lecture context in grades | Make sure Step 1 (indexing) completed successfully and the `--chroma-path` matches the path in `scripts/web/app.py` |
| API key errors | Confirm the correct key is set in `.env` and the file is in the project root |
