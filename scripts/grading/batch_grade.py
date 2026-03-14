#!/usr/bin/env python3
"""
batch_grade.py — Grade all students across all assignments in one run.

Discovers unique (semester, student_id, assignment_id) combinations from
chunks.jsonl, runs run_grading() for each, and writes a summary CSV.

Usage:
    python grading/batch_grade.py \
        --chunks-jsonl outputs/.../chunks.jsonl \
        --retrieval-dir outputs/.../  \       # dir containing retrieval_assignmentN.jsonl files
        --out-dir outputs/.../grading \
        --grading-model gpt-4o-2024-11-20
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.config import load_environment
from grading.grade_submission import run_grading


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def discover_student_assignments(chunks_jsonl: Path) -> list[dict]:
    """
    Return a list of dicts, one per unique student×assignment, with keys:
        semester, student_id, assignment_id, student_path_filter, source_paths
    """
    chunks = read_jsonl(chunks_jsonl)

    # Group by (semester, student_id, assignment_id)
    groups: dict[tuple, dict] = {}
    for c in chunks:
        md = c.get("metadata", {})
        if str(md.get("source_type", "")).lower() != "student":
            continue

        source_path = str(md.get("source_path", ""))
        assignment_id = str(md.get("assignment_id", "")) or None

        # Parse semester and student_id from path like:
        # Student_Submissions/fall_2024/student_1/Assignment_1/submission.pdf
        parts = Path(source_path).parts
        semester = None
        student_id = None
        for i, part in enumerate(parts):
            if part.lower().endswith("_2024") or part.lower().endswith("_2025"):
                semester = part
                if i + 1 < len(parts):
                    student_id = parts[i + 1]
                break

        if not semester or not student_id or not assignment_id:
            continue

        key = (semester, student_id, assignment_id)
        if key not in groups:
            # Build the most specific unambiguous filter path
            # e.g. "fall_2024/student_1/Assignment_1"
            filter_path = f"{semester}/{student_id}/Assignment_{assignment_id}" \
                if assignment_id.isdigit() else f"{semester}/{student_id}/{assignment_id.replace('_', '_').title().replace('_', '_')}"
            # Fallback: derive from source_path
            try:
                idx = source_path.index(semester)
                parts_after = source_path[idx:]
                # Take up to the assignment folder level
                p = Path(parts_after)
                filter_path = str(Path(*p.parts[:3])) if len(p.parts) >= 3 else parts_after
            except ValueError:
                pass

            groups[key] = {
                "semester": semester,
                "student_id": student_id,
                "assignment_id": assignment_id,
                "student_path_filter": filter_path,
                "source_paths": [],
            }
        groups[key]["source_paths"].append(source_path)

    result = sorted(groups.values(), key=lambda g: (g["semester"], g["student_id"], g["assignment_id"]))
    return result


# ---------------------------------------------------------------------------
# Retrieval file resolution
# ---------------------------------------------------------------------------

ASSIGNMENT_ID_ALIASES = {
    "course_project": ["course_project", "project"],
    "quiz_1": ["quiz_1", "quiz1"],
    "quiz_2": ["quiz_2", "quiz2"],
    "quiz_3": ["quiz_3", "quiz3"],
}


def find_retrieval_file(retrieval_dir: Path, assignment_id: str) -> Path | None:
    """
    Look for retrieval_assignment{N}.jsonl or retrieval_{assignment_id}.jsonl
    inside retrieval_dir.
    """
    candidates = [
        retrieval_dir / f"retrieval_assignment{assignment_id}.jsonl",
        retrieval_dir / f"retrieval_{assignment_id}.jsonl",
    ]
    for alias in ASSIGNMENT_ID_ALIASES.get(assignment_id, []):
        candidates.append(retrieval_dir / f"retrieval_{alias}.jsonl")

    for c in candidates:
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------------------
# CSV summary
# ---------------------------------------------------------------------------

def write_summary_csv(results: list[dict], out_path: Path) -> None:
    if not results:
        return
    fieldnames = [
        "semester", "student_id", "assignment_id",
        "overall_score", "overall_feedback", "scoring_mode",
        "student_path_filter", "error",
    ]
    # Collect all question IDs seen
    all_q_ids: list[str] = []
    seen_q: set[str] = set()
    for r in results:
        for q in r.get("questions", []):
            qid = q.get("question_id", "")
            if qid and qid not in seen_q:
                seen_q.add(qid)
                all_q_ids.append(qid)

    for qid in all_q_ids:
        fieldnames.append(f"score_{qid}")
        fieldnames.append(f"feedback_{qid}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row: dict = {
                "semester": r.get("semester", ""),
                "student_id": r.get("student_id", ""),
                "assignment_id": r.get("assignment_id", ""),
                "overall_score": r.get("overall_score", ""),
                "overall_feedback": r.get("overall_feedback", ""),
                "scoring_mode": r.get("scoring_mode", ""),
                "student_path_filter": r.get("student_path_filter", ""),
                "error": r.get("error", ""),
            }
            for q in r.get("questions", []):
                qid = q.get("question_id", "")
                if qid:
                    row[f"score_{qid}"] = q.get("score", "")
                    row[f"feedback_{qid}"] = q.get("feedback", "")
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch grade all students across all assignments")
    parser.add_argument("--chunks-jsonl", required=True, help="Path to chunks.jsonl")
    parser.add_argument(
        "--retrieval-dir", required=True,
        help="Directory containing retrieval_assignmentN.jsonl files",
    )
    parser.add_argument("--out-dir", required=True, help="Root output directory for grades")
    parser.add_argument("--grading-model", default="gpt-4o-2024-11-20")
    parser.add_argument("--max-lecture-chars", type=int, default=12000)
    parser.add_argument("--max-student-chars", type=int, default=20000)
    parser.add_argument("--max-rubric-chars", type=int, default=8000)
    parser.add_argument("--max-assignment-chars", type=int, default=6000)
    parser.add_argument("--max-reference-chars", type=int, default=8000)
    parser.add_argument(
        "--assignment-ids", nargs="*", default=None,
        help="Limit to specific assignment IDs (e.g. 1 2 3). Default: all found.",
    )
    parser.add_argument(
        "--semesters", nargs="*", default=None,
        help="Limit to specific semesters (e.g. fall_2024). Default: all found.",
    )
    parser.add_argument(
        "--students", nargs="*", default=None,
        help="Limit to specific student IDs (e.g. student_1 student_2). Default: all found.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true", default=True,
        help="Skip if grades.json already exists for this student+assignment (default: true)",
    )
    parser.add_argument(
        "--no-skip-existing", dest="skip_existing", action="store_false",
        help="Re-grade even if grades.json already exists",
    )
    return parser.parse_args()


def main() -> int:
    load_environment()
    args = parse_args()

    chunks_jsonl = Path(args.chunks_jsonl).expanduser().resolve()
    retrieval_dir = Path(args.retrieval_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    print(f"Discovering student assignments from: {chunks_jsonl}")
    all_entries = discover_student_assignments(chunks_jsonl)
    print(f"Found {len(all_entries)} student×assignment combinations")

    # Apply filters
    if args.assignment_ids:
        all_entries = [e for e in all_entries if e["assignment_id"] in args.assignment_ids]
    if args.semesters:
        all_entries = [e for e in all_entries if e["semester"] in args.semesters]
    if args.students:
        all_entries = [e for e in all_entries if e["student_id"] in args.students]

    print(f"After filters: {len(all_entries)} to grade\n")

    summary_rows: list[dict] = []
    skipped = 0
    succeeded = 0
    failed = 0

    for i, entry in enumerate(all_entries, 1):
        semester = entry["semester"]
        student_id = entry["student_id"]
        assignment_id = entry["assignment_id"]
        student_path_filter = entry["student_path_filter"]

        # Output dir per student/assignment
        grade_dir = out_dir / semester / student_id / f"Assignment_{assignment_id}"
        grade_dir.mkdir(parents=True, exist_ok=True)
        grades_path = grade_dir / "grades.json"

        label = f"[{i}/{len(all_entries)}] {semester}/{student_id}/Assignment_{assignment_id}"

        if args.skip_existing and grades_path.exists():
            print(f"  SKIP  {label} (already graded)")
            # Load existing for summary
            try:
                existing = json.loads(grades_path.read_text(encoding="utf-8"))
                summary_rows.append({
                    **entry,
                    "overall_score": existing.get("overall_score", ""),
                    "overall_feedback": existing.get("overall_feedback", ""),
                    "scoring_mode": existing.get("scoring_mode", ""),
                    "questions": existing.get("questions", []),
                    "error": "",
                })
            except Exception:
                pass
            skipped += 1
            continue

        # Find retrieval file
        retrieval_jsonl = find_retrieval_file(retrieval_dir, assignment_id)
        if retrieval_jsonl is None:
            print(f"  SKIP  {label} — no retrieval file for assignment_id={assignment_id}")
            summary_rows.append({**entry, "overall_score": "", "overall_feedback": "",
                                  "scoring_mode": "", "questions": [],
                                  "error": f"no retrieval file for assignment_id={assignment_id}"})
            skipped += 1
            continue

        print(f"  GRADE {label}")
        print(f"        filter={student_path_filter}  retrieval={retrieval_jsonl.name}")

        try:
            run_grading(
                retrieval_jsonl=retrieval_jsonl,
                chunks_jsonl=chunks_jsonl,
                student_path_filter=student_path_filter,
                out_dir=grade_dir,
                model=args.grading_model,
                max_lecture_chars=args.max_lecture_chars,
                max_student_chars=args.max_student_chars,
                assignment_id=assignment_id,
                max_rubric_chars=args.max_rubric_chars,
                max_assignment_chars=args.max_assignment_chars,
                max_reference_chars=args.max_reference_chars,
            )
            result = json.loads(grades_path.read_text(encoding="utf-8"))
            summary_rows.append({
                **entry,
                "overall_score": result.get("overall_score", ""),
                "overall_feedback": result.get("overall_feedback", ""),
                "scoring_mode": result.get("scoring_mode", ""),
                "questions": result.get("questions", []),
                "error": "",
            })
            succeeded += 1
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"  ERROR {label}: {exc}")
            print(tb)
            summary_rows.append({**entry, "overall_score": "", "overall_feedback": "",
                                  "scoring_mode": "", "questions": [], "error": str(exc)})
            failed += 1

    # Write summary CSV
    csv_path = out_dir / "grades_summary.csv"
    write_summary_csv(summary_rows, csv_path)

    print(f"\n{'='*60}")
    print(f"Batch grading complete")
    print(f"  Succeeded : {succeeded}")
    print(f"  Skipped   : {skipped}")
    print(f"  Failed    : {failed}")
    print(f"  Summary   : {csv_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
