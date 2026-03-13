#!/usr/bin/env python3
"""
grade_submission.py — Grade a student submission using retrieved lecture context.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Allow `from core.config import ...` regardless of where the script is run from.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.config import get_api_key, load_environment


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def anonymize_label(text: str) -> str:
    """Remove quality hints such as 'good example' / 'bad example' from labels."""
    t = str(text or "")
    t = re.sub(r"(?i)\bgood\s+example\b", "example", t)
    t = re.sub(r"(?i)\bbad\s+example\b", "example", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_student_content(
    chunks_jsonl: Path,
    student_path_filter: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Return (assembled_text, student_chunks) sorted by document_order.
    If student_path_filter is given, only include chunks whose source_path
    contains that substring (case-insensitive).
    """
    all_chunks = read_jsonl(chunks_jsonl)
    student_chunks = [
        c for c in all_chunks
        if str(c.get("metadata", {}).get("source_type", "")).lower() == "student"
    ]
    if student_path_filter:
        filt = student_path_filter.lower()
        student_chunks = [
            c for c in student_chunks
            if filt in str(c.get("metadata", {}).get("source_path", "")).lower()
        ]

    student_chunks.sort(
        key=lambda c: (
            c.get("metadata", {}).get("document_order", 9999),
            c.get("metadata", {}).get("sort_key", ""),
        )
    )

    parts: list[str] = []
    seen: set[str] = set()
    for c in student_chunks:
        text = str(c.get("content", "")).strip()
        if text and text not in seen:
            seen.add(text)
            parts.append(text)

    return "\n\n".join(parts), student_chunks


def load_chunks_by_type(
    chunks_jsonl: Path,
    source_type: str,
    path_filter: str | None = None,
    max_chars: int = 0,
    assignment_id: str | None = None,
) -> str:
    """
    Assemble text from chunks.jsonl where metadata.source_type == source_type.

    Filtering priority (most specific first):
      1. assignment_id — exact match on metadata.assignment_id (e.g. "1", "2").
         Use this to unambiguously select one assignment's rubric/description.
      2. path_filter — substring match on metadata.source_path (case-insensitive).
         Falls back to this when assignment_id is not set.
    If max_chars > 0, output is truncated at that character limit.
    """
    all_chunks = read_jsonl(chunks_jsonl)
    matched = [
        c for c in all_chunks
        if str(c.get("metadata", {}).get("source_type", "")).lower() == source_type.lower()
    ]
    if assignment_id is not None:
        matched = [
            c for c in matched
            if str(c.get("metadata", {}).get("assignment_id", "")) == str(assignment_id)
        ]
    elif path_filter:
        filt = path_filter.lower()
        matched = [
            c for c in matched
            if filt in str(c.get("metadata", {}).get("source_path", "")).lower()
        ]
    matched.sort(
        key=lambda c: (
            c.get("metadata", {}).get("document_order", 9999),
            c.get("metadata", {}).get("sort_key", ""),
        )
    )
    parts: list[str] = []
    seen: set[str] = set()
    total = 0
    for c in matched:
        text = str(c.get("content", "")).strip()
        if not text or text in seen:
            continue
        if max_chars > 0 and total + len(text) > max_chars:
            break
        seen.add(text)
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts)


def load_lecture_context(
    retrieval_jsonl: Path,
    student_path_filter: str | None,
    max_chars: int,
) -> str:
    """
    Collect unique lecture snippets from retrieval_results.jsonl,
    ordered by how often they appear (most-retrieved first).

    The preferred workflow queries ChromaDB with rubric/assignment chunks (not student
    chunks), so retrieval_results.jsonl is assignment-scoped.  In that case
    student_path_filter is ignored (every row in the file is already for this assignment).

    Legacy behaviour: if rows carry 'student_source_path', apply student_path_filter.
    """
    rows = read_jsonl(retrieval_jsonl)

    # Detect whether this is rubric-based retrieval (new) or student-based (legacy).
    # New format uses 'query_source_path'; legacy uses 'student_source_path'.
    is_rubric_based = rows and "query_source_path" in rows[0]

    if not is_rubric_based and student_path_filter:
        # Legacy: filter to rows that belong to this student.
        filt = student_path_filter.lower()
        rows = [
            r for r in rows
            if filt in str(r.get("student_source_path", "")).lower()
        ]

    # Count how often each lecture document appears across all query rows.
    doc_counts: dict[str, int] = {}
    for row in rows:
        results = row.get("results", {})
        docs = results.get("documents", [[]])[0]
        for doc in docs:
            doc = str(doc).strip()
            if doc:
                doc_counts[doc] = doc_counts.get(doc, 0) + 1

    # Sort most-retrieved first, then assemble up to max_chars.
    ranked = sorted(doc_counts.items(), key=lambda x: -x[1])
    parts: list[str] = []
    total = 0
    for doc, _ in ranked:
        if total + len(doc) > max_chars:
            break
        parts.append(doc)
        total += len(doc)

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

GENERIC_SYSTEM_PROMPT = """\
You are grading a complete student assignment in a graduate Health Informatics course.

Grade against the assignment instructions if provided. Otherwise grade on completeness,
correctness, and coherence.

SCORING MODEL — grade each criterion below as a question (score out of its stated max):
- Q1  requirement_coverage (0-30): Did the student address all requested tasks/sections?
- Q2  correctness_and_reasoning (0-25): Are claims/processes accurate and logically sound?
- Q3  workflow_and_structure_quality (0-20): Is the process/workflow clear, ordered, and usable?
- Q4  lecture_alignment (0-15): Uses relevant course concepts/terminology appropriately.
- Q5  clarity_and_professionalism (0-10): Clear writing, organisation, actionable details.

CALIBRATION (as % of each criterion's max):
- 95-100%: Excellent — all criteria fully addressed.
- 85-94%: Strong — minor gaps only.
- 70-84%: Good — solid but missing some depth.
- 50-69%: Partial — limited coverage or significant issues.
- <50%: Inadequate — major criteria missing or incorrect.

BLIND GRADING: Ignore filenames, folder names, IDs, or quality-hint words like "good/bad".
Grade only the submission content.

DEDUCTION POLICY:
- Deduct only for explicitly missing or incorrect required content.
- Do NOT deduct for optional enhancements or stylistic preferences.
- If information appears in diagram/image-extracted text, count it as valid evidence.

Return ONLY valid JSON (no markdown, no extra text):
{
  "student_file": "<filename>",
  "overall_score": <sum of all question scores, float 0-100>,
  "overall_feedback": "<2-4 sentences summarising overall performance>",
  "questions": [
    {
      "question_id": "<Q1..Q5>",
      "question_summary": "<criterion name in one sentence>",
      "student_answer_summary": "<2-3 sentences describing what the student actually submitted for this criterion>",
      "score": <points awarded, float within criterion max>,
      "feedback": "<2-3 sentences of specific, evidence-based feedback for this criterion>"
    }
  ],
  "strengths": ["<short bullet>"],
  "gaps": ["<short bullet>"],
  "action_items": ["<specific, actionable improvement>"],
  "confidence": <float 0-1>
}
"""

RUBRIC_SYSTEM_PROMPT = """\
You are grading a complete student assignment in a graduate Health Informatics course.

A RUBRIC is provided. You MUST:
1. Parse the rubric to identify each scored section and its maximum point value.
2. Grade the student strictly against each rubric section's criteria.
3. For each rubric section produce one entry in the "questions" array.
4. overall_score = sum of all question scores (must be <= 100).

CALIBRATION (per rubric section, as % of section max):
- 95-100%: Thorough and complete — all required elements addressed with reasonable depth.
- 85-94%: Complete with one or two minor omissions.
- 70-84%: Most criteria addressed but notable gaps in required elements.
- 50-69%: Partial — several required criteria missing or significantly underdeveloped.
- <50%: Inadequate — majority of criteria missing or incorrect.

GRADING PHILOSOPHY: This is a graduate professional course. A student who addresses all required
rubric criteria with reasonable effort and depth earns near-full credit (95-100%). Only deduct
meaningfully when required criteria are clearly absent or incorrect. Do NOT deduct for:
- Imperfect wording or style
- Missing optional enhancements
- Depth beyond what was required
- Presentation choices

BLIND GRADING: Ignore filenames, folder names, IDs, or quality-hint words like "good/bad example".
Grade only the submission content.

DEDUCTION POLICY:
- Start at full credit for each section and deduct only for explicitly missing or incorrect required criteria.
- If information appears in diagram/image-extracted text, count it as valid evidence.
- Small omissions (one missing element out of many) warrant small deductions (1-3 pts), not large ones.

Return ONLY valid JSON (no markdown, no extra text):
{
  "student_file": "<filename>",
  "overall_score": <sum of all question scores, float 0-100>,
  "overall_feedback": "<2-4 sentences summarising performance against the rubric>",
  "questions": [
    {
      "question_id": "<Q1, Q2, ...>",
      "question_summary": "<rubric section name / criterion in one sentence>",
      "student_answer_summary": "<2-3 sentences describing what the student actually submitted for this criterion>",
      "score": <points awarded for this section, float within section max>,
      "feedback": "<2-3 sentences of specific, evidence-based feedback for this criterion>"
    }
  ],
  "strengths": ["<short bullet referencing a rubric criterion met well>"],
  "gaps": ["<short bullet referencing a rubric criterion not met or weak>"],
  "action_items": ["<specific, actionable improvement tied to rubric criterion>"],
  "confidence": <float 0-1>
}
"""

# Backward-compat alias used by callers that reference SYSTEM_PROMPT directly.
SYSTEM_PROMPT = GENERIC_SYSTEM_PROMPT


def select_system_prompt(has_rubric: bool) -> str:
    return RUBRIC_SYSTEM_PROMPT if has_rubric else GENERIC_SYSTEM_PROMPT


def detect_expected_sections(text: str) -> list[str]:
    """
    Detect numbered sections like:
      1. ...       Q1. ...
      2) ...       Q2. ...
      Section 3 ...
    Returns stable IDs: ["Q1", "Q2", ...]
    """
    # Match both plain numbers (1. 2)) and Q-prefixed (Q1. Q2.)
    matches = re.findall(
        r"(?im)^\s*(?:section\s+)?(?:Q)?([1-9][0-9]?)\s*[\.\)]\s+",
        text or "",
    )
    ordered_unique: list[int] = []
    seen: set[int] = set()
    for m in matches:
        n = int(m)
        if n not in seen:
            seen.add(n)
            ordered_unique.append(n)
    ordered_unique.sort()
    return [f"Q{n}" for n in ordered_unique]


def build_user_message(
    student_text: str,
    lecture_context: str,
    student_file: str,
    rubric_text: str,
    assignment_text: str,
    expected_sections: list[str],
    max_student_chars: int,
) -> str:
    student_excerpt = student_text[:max_student_chars]
    if len(student_text) > max_student_chars:
        student_excerpt += "\n\n[... content truncated ...]"

    if expected_sections:
        sections_line = (
            f"Grade EXACTLY these questions: {', '.join(expected_sections)}. "
            "Return one JSON object per question. Use question_summary from ASSIGNMENT INSTRUCTIONS."
        )
    elif assignment_text:
        sections_line = (
            "Grade EXACTLY the questions listed in ASSIGNMENT INSTRUCTIONS above. "
            "Return one JSON object per question. Use question_summary from ASSIGNMENT INSTRUCTIONS."
        )
    else:
        sections_line = "(No assignment provided — grade all sections you can identify from the student submission)"

    return (
        f"STUDENT FILE: {student_file}\n\n"
        "=== ASSIGNMENT INSTRUCTIONS (grade these questions) ===\n"
        f"{assignment_text or '(No assignment instructions provided — infer questions from student submission)'}\n\n"
        "=== EXPECTED SECTIONS ===\n"
        f"{sections_line}\n\n"
        "=== RUBRIC ===\n"
        f"{rubric_text or '(No rubric provided)'}\n\n"
        "=== LECTURE CONTEXT ===\n"
        f"{lecture_context}\n\n"
        "=== STUDENT SUBMISSION ===\n"
        f"{student_excerpt}"
    )


def read_text_file(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


# ---------------------------------------------------------------------------
# OpenAI API call
# ---------------------------------------------------------------------------

def call_openai(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str,
) -> dict[str, Any]:
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError("openai package not installed. Run: pip install openai") from exc

    client = openai.OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"raw_response": raw, "parse_error": True}

    return {
        "result": parsed,
        "usage": {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        },
        "model": response.model,
    }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def normalize_grade_result(
    result: dict[str, Any],
    *,
    expected_sections: list[str],
    student_file: str,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "student_file": student_file,
            "overall_score": 0.0,
            "overall_feedback": "Invalid grading response format.",
            "questions": [],
            "parse_error": True,
        }

    # Preferred holistic schema
    score_breakdown_raw = result.get("score_breakdown", {})
    score_breakdown: dict[str, float] = {}
    GENERIC_LIMITS = {
        "requirement_coverage": 30.0,
        "correctness_and_reasoning": 25.0,
        "workflow_and_structure_quality": 20.0,
        "lecture_alignment": 15.0,
        "clarity_and_professionalism": 10.0,
    }
    if isinstance(score_breakdown_raw, dict):
        generic_keys = set(GENERIC_LIMITS.keys())
        result_keys = set(score_breakdown_raw.keys())
        if result_keys & generic_keys:
            # Generic scoring model — validate against known limits
            for key, maxv in GENERIC_LIMITS.items():
                val = _to_float(score_breakdown_raw.get(key))
                if val is None:
                    continue
                score_breakdown[key] = round(max(0.0, min(maxv, val)), 2)
        else:
            # Rubric-defined categories — accept as-is, values are the LLM's point awards
            for key, val in score_breakdown_raw.items():
                v = _to_float(val)
                if v is None:
                    continue
                score_breakdown[key] = round(max(0.0, v), 2)

    strengths_raw = result.get("strengths", [])
    gaps_raw = result.get("gaps", [])
    actions_raw = result.get("action_items", [])
    strengths = [str(x).strip() for x in strengths_raw] if isinstance(strengths_raw, list) else []
    gaps = [str(x).strip() for x in gaps_raw] if isinstance(gaps_raw, list) else []
    action_items = [str(x).strip() for x in actions_raw] if isinstance(actions_raw, list) else []

    strengths = [x for x in strengths if x]
    gaps = [x for x in gaps if x]
    action_items = [x for x in action_items if x]

    # Backward-compatible question parsing (if model returns questions)
    questions_raw = result.get("questions", [])
    questions: list[dict[str, Any]] = []
    if isinstance(questions_raw, list):
        for idx, q in enumerate(questions_raw, 1):
            if not isinstance(q, dict):
                continue
            score = _to_float(q.get("score"))
            if score is None:
                continue
            score = max(0.0, score)
            qid = str(q.get("question_id", f"Q{idx}")).strip() or f"Q{idx}"
            questions.append(
                {
                    "question_id": qid,
                    "question_summary": str(q.get("question_summary", "")).strip(),
                    "student_answer_summary": str(q.get("student_answer_summary", "")).strip(),
                    "on_topic": q.get("on_topic"),
                    "score": round(score, 2),
                    "met_criteria": q.get("met_criteria", []),
                    "gaps": q.get("gaps", []),
                    "feedback": str(q.get("feedback", "")).strip(),
                    "evidence_refs": q.get("evidence_refs", []),
                    "confidence": _to_float(q.get("confidence")) if q.get("confidence") is not None else None,
                }
            )

    # Compute final overall score robustly
    overall_score = None
    if score_breakdown:
        overall_score = round(sum(score_breakdown.values()), 2)
    elif questions:
        overall_score = round(sum(q["score"] for q in questions), 2)
    else:
        model_score = _to_float(result.get("overall_score"))
        overall_score = round(max(0.0, min(100.0, model_score)), 2) if model_score is not None else 0.0

    overall_feedback = str(result.get("overall_feedback", "")).strip()
    if not overall_feedback:
        overall_feedback = "Automated grading completed."

    if expected_sections and questions and len(questions) < len(expected_sections):
        overall_feedback += (
            f" Note: expected {len(expected_sections)} sections ({', '.join(expected_sections)}), "
            f"but model returned {len(questions)} section grades."
        )

    return {
        "student_file": str(result.get("student_file", student_file)).strip() or student_file,
        "overall_score": overall_score,
        "overall_feedback": overall_feedback,
        "score_breakdown": score_breakdown,
        "strengths": strengths,
        "gaps": gaps,
        "action_items": action_items,
        "confidence": _to_float(result.get("confidence")) if result.get("confidence") is not None else None,
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade a student submission via OpenAI")
    parser.add_argument("--retrieval-jsonl", required=True, help="Path to retrieval_results.jsonl")
    parser.add_argument("--chunks-jsonl", required=True, help="Path to chunks.jsonl")
    parser.add_argument(
        "--student-path",
        default=None,
        help="Substring to filter to a single student file (e.g. 'Student 1')",
    )
    parser.add_argument("--out-dir", default=None, help="Output directory (default: alongside retrieval-jsonl)")
    parser.add_argument("--model", default="gpt-4o-2024-11-20", help="OpenAI model to use")
    parser.add_argument("--max-lecture-chars", type=int, default=12000)
    parser.add_argument("--max-student-chars", type=int, default=20000)
    parser.add_argument(
        "--rubric-file",
        default=None,
        help="Legacy: standalone rubric text file. Use --rubric-path instead when rubric was "
             "extracted through the pipeline.",
    )
    parser.add_argument(
        "--assignment-file",
        default=None,
        help="Legacy: standalone assignment text file. Use --assignment-path instead.",
    )
    parser.add_argument(
        "--assignment-id",
        default=None,
        help="Assignment number (e.g. '1' or '2'). Preferred over --rubric-path / "
             "--assignment-path: unambiguously selects chunks by metadata.assignment_id.",
    )
    parser.add_argument(
        "--rubric-path",
        default=None,
        help="Source-path substring to identify rubric chunks inside chunks.jsonl "
             "(e.g. 'Assignment 1' or 'Grading Rubric'). Ignored when --assignment-id is set.",
    )
    parser.add_argument(
        "--assignment-path",
        default=None,
        help="Source-path substring to identify assignment description chunks "
             "(e.g. 'Assignment1_Description').",
    )
    parser.add_argument(
        "--reference-path",
        default=None,
        help="Source-path substring to include reference material chunks "
             "(e.g. 'HIMSS' or 'relevant_material').",
    )
    parser.add_argument("--max-rubric-chars", type=int, default=8000)
    parser.add_argument("--max-assignment-chars", type=int, default=6000)
    parser.add_argument("--max-reference-chars", type=int, default=8000)
    return parser.parse_args()


def run_grading(
    *,
    retrieval_jsonl: Path,
    chunks_jsonl: Path,
    student_path_filter: str | None,
    out_dir: Path,
    model: str,
    max_lecture_chars: int,
    max_student_chars: int,
    rubric_file: Path | None = None,
    assignment_file: Path | None = None,
    rubric_path_filter: str | None = None,
    assignment_path_filter: str | None = None,
    reference_path_filter: str | None = None,
    assignment_id: str | None = None,
    max_rubric_chars: int = 8000,
    max_assignment_chars: int = 6000,
    max_reference_chars: int = 8000,
) -> Path:
    """
    Grade a student submission.

    Rubric and assignment text can be supplied in order of preference:
      1. assignment_id — exact match on metadata.assignment_id (e.g. "1" or "2").
         Unambiguously selects one assignment's rubric and description from chunks.jsonl.
      2. rubric_path_filter / assignment_path_filter — substring match on source_path.
      3. rubric_file / assignment_file — legacy standalone text files.
    """
    api_key = get_api_key("openai")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file.")

    if not retrieval_jsonl.exists():
        raise RuntimeError(f"retrieval_results.jsonl not found: {retrieval_jsonl}")
    if not chunks_jsonl.exists():
        raise RuntimeError(f"chunks.jsonl not found: {chunks_jsonl}")

    # --- Student content from chunks.jsonl ---
    print(f"Loading student content from: {chunks_jsonl}")
    student_text, student_chunks = load_student_content(chunks_jsonl, student_path_filter)
    if not student_text:
        raise RuntimeError("No student content found. Check --student-path filter or chunks.jsonl.")
    student_file = "unknown"
    if student_chunks:
        student_file = student_chunks[0].get("metadata", {}).get("filename", "unknown")
    print(f"Student file: {student_file}  |  {len(student_text):,} chars  |  {len(student_chunks)} chunks")

    student_file_for_model = anonymize_label(student_file)

    print(f"Loading lecture context from: {retrieval_jsonl}")
    lecture_context = load_lecture_context(retrieval_jsonl, student_path_filter, max_lecture_chars)
    print(f"Lecture context: {len(lecture_context):,} chars")

    if not lecture_context:
        print("WARNING: No lecture context found. Grading without lecture reference.")
        lecture_context = "(No lecture context available)"

    # --- Rubric: prefer assignment_id > path_filter > text file ---
    rubric_text = ""
    if assignment_id is not None or rubric_path_filter is not None:
        rubric_text = load_chunks_by_type(
            chunks_jsonl, "rubric", rubric_path_filter, max_rubric_chars,
            assignment_id=assignment_id,
        )
        if rubric_text:
            label = f"assignment_id={assignment_id}" if assignment_id else rubric_path_filter
            print(f"Rubric loaded from chunks ({label}): {len(rubric_text):,} chars")
    if not rubric_text and rubric_file:
        rubric_text = read_text_file(rubric_file)
        if rubric_text:
            print(f"Rubric loaded from file: {len(rubric_text):,} chars")
        else:
            print(f"WARNING: rubric file not found/empty: {rubric_file}")

    # --- Assignment description: prefer assignment_id > path_filter > text file ---
    assignment_text = ""
    if assignment_id is not None or assignment_path_filter is not None:
        assignment_text = load_chunks_by_type(
            chunks_jsonl, "assignment", assignment_path_filter, max_assignment_chars,
            assignment_id=assignment_id,
        )
        if assignment_text:
            label = f"assignment_id={assignment_id}" if assignment_id else assignment_path_filter
            print(f"Assignment loaded from chunks ({label}): {len(assignment_text):,} chars")
    if not assignment_text and assignment_file:
        assignment_text = read_text_file(assignment_file)
        if assignment_text:
            print(f"Assignment instructions loaded from file: {len(assignment_text):,} chars")
        else:
            print(f"WARNING: assignment file not found/empty: {assignment_file}")
    if not assignment_text:
        print("WARNING: No assignment instructions — model will infer questions from student text.")

    # --- Optional reference material (e.g. VWC HIMSS Davies doc) ---
    reference_text = ""
    if reference_path_filter is not None:
        reference_text = load_chunks_by_type(
            chunks_jsonl, "assignment", reference_path_filter, max_reference_chars
        )
        if reference_text:
            print(f"Reference material loaded from chunks ({reference_path_filter}): {len(reference_text):,} chars")

    # Prefer sections from assignment instructions (authoritative) over student text.
    # This ensures that even if a student's text has no numbered headings, the
    # grader uses the actual assignment questions (Q1-Q4) as the grading template.
    if assignment_text:
        expected_sections = detect_expected_sections(assignment_text)
        if expected_sections:
            print(f"Expected sections (from assignment): {expected_sections}")
        else:
            print("WARNING: Could not detect section IDs from assignment file.")
    else:
        expected_sections = []

    # Fall back to student text detection only if no assignment sections found.
    if not expected_sections:
        expected_sections = detect_expected_sections(student_text)
        if expected_sections:
            print(f"Expected sections (from student text): {expected_sections}")

    # Merge reference material into assignment context so the LLM can draw on it.
    if reference_text:
        assignment_text = (assignment_text + "\n\n=== REFERENCE MATERIAL ===\n" + reference_text
                          if assignment_text else reference_text)

    user_msg = build_user_message(
        student_text=student_text,
        lecture_context=lecture_context,
        student_file=student_file_for_model,
        rubric_text=rubric_text,
        assignment_text=assignment_text,
        expected_sections=expected_sections,
        max_student_chars=max_student_chars,
    )

    system_prompt = select_system_prompt(has_rubric=bool(rubric_text))
    mode_label = "rubric-aware" if rubric_text else "generic"
    print(f"Calling OpenAI ({model}) [{mode_label} scoring] ...")
    response = call_openai(
        model=model,
        api_key=api_key,
        system=system_prompt,
        user=user_msg,
    )

    usage = response["usage"]
    print(f"Tokens used — input: {usage['input_tokens']:,}  output: {usage['output_tokens']:,}")

    normalized = normalize_grade_result(
        response["result"],
        expected_sections=expected_sections,
        student_file=student_file,
    )

    output = {
        "grading_model": response["model"],
        "scoring_mode": "rubric_aware" if rubric_text else "generic",
        "student_path_filter": student_path_filter,
        "chunks_jsonl": str(chunks_jsonl),
        "retrieval_jsonl": str(retrieval_jsonl),
        "rubric_source": rubric_path_filter or (str(rubric_file) if rubric_file else None),
        "assignment_source": assignment_path_filter or (str(assignment_file) if assignment_file else None),
        "reference_source": reference_path_filter,
        "expected_sections": expected_sections,
        "token_usage": usage,
        **normalized,
    }

    out_path = out_dir / "grades.json"
    write_json(out_path, output)
    print(f"\nGrades written to: {out_path}")

    # Print a quick summary.
    result = normalized
    if not result.get("parse_error"):
        print(f"\nOverall score: {result.get('overall_score', 'N/A')} / 100")
        print(f"Overall feedback: {result.get('overall_feedback', '')}")
        breakdown = result.get("score_breakdown", {})
        if isinstance(breakdown, dict) and breakdown:
            print("\nScore breakdown:")
            for k, v in breakdown.items():
                print(f"  {k}: {v}")
        questions = result.get("questions", [])
        if questions:
            print(f"\nPer-question breakdown ({len(questions)} questions):")
            for q in questions:
                print(f"  [{q.get('question_id')}] {q.get('score')} pts — {q.get('feedback', '')[:80]}")
    else:
        print("\nWARNING: Could not parse JSON from OpenAI response. Raw output saved in grades.json.")

    return out_path


def main() -> int:
    load_environment()
    args = parse_args()

    retrieval_jsonl = Path(args.retrieval_jsonl).expanduser().resolve()
    chunks_jsonl = Path(args.chunks_jsonl).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else retrieval_jsonl.parent
    rubric_file = Path(args.rubric_file).expanduser().resolve() if args.rubric_file else None
    assignment_file = Path(args.assignment_file).expanduser().resolve() if args.assignment_file else None

    run_grading(
        retrieval_jsonl=retrieval_jsonl,
        chunks_jsonl=chunks_jsonl,
        student_path_filter=args.student_path,
        out_dir=out_dir,
        model=args.model,
        max_lecture_chars=int(args.max_lecture_chars),
        max_student_chars=int(args.max_student_chars),
        rubric_file=rubric_file,
        assignment_file=assignment_file,
        assignment_id=args.assignment_id,
        rubric_path_filter=args.rubric_path,
        assignment_path_filter=args.assignment_path,
        reference_path_filter=args.reference_path,
        max_rubric_chars=int(args.max_rubric_chars),
        max_assignment_chars=int(args.max_assignment_chars),
        max_reference_chars=int(args.max_reference_chars),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
