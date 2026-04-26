#!/usr/bin/env python3
"""
Re-grade Quiz 1 (Question 13, BPR / EHR) with the few-shot–calibrated system prompt.

The prompt lives in:
  `scripts/grading/calibrations/quiz1_q13_bpr_system_prompt.txt`
It includes the official sub-rubric, anti–keyword-gating rules, and **ANCHOR EXAMPLES**
(few-shot calibration) so scores track instructor expectations.

Before running:
1. `export OPENAI_API_KEY=...`
2. `pip install openpyxl openai`
3. Pass your anonymized comparison spreadsheet path (see --help).

Output:
- Side-by-side: prior AI | new AI | human (if columns present)
- Summary statistics and optional `quiz1_regrade_results.json`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any

# For calling OpenAI API
try:
    import openai
except ImportError:
    print("ERROR: openai not installed. Run: pip install openai")
    sys.exit(1)

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not installed. Run: pip install openpyxl")
    sys.exit(1)


DEFAULT_MODEL = "gpt-4o-2024-11-20"
_CALIB_DIR = Path(__file__).resolve().parent / "calibrations"
_DEFAULT_PROMPT_FILE = _CALIB_DIR / "quiz1_q13_bpr_system_prompt.txt"


def is_nonrecoverable_api_error(exception: Exception) -> bool:
    text = str(exception).lower()
    return "invalid_api_key" in text or "permission_denied" in text or "authorization" in text


def load_system_prompt(prompt_path: Path) -> str:
    if not prompt_path.is_file():
        print(f"ERROR: Prompt file not found: {prompt_path}")
        sys.exit(1)
    return prompt_path.read_text(encoding="utf-8").strip() + "\n"


def get_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: OPENAI_API_KEY not set. Please export it first:")
        print('  export OPENAI_API_KEY="sk-..."')
        sys.exit(1)
    return key


def load_quiz1_data(
    excel_path: Path,
    worksheet: str,
    col_student: int,
    col_old_ai: int,
    col_human: int,
    col_answer: int,
    min_data_row: int,
) -> list[dict[str, Any]]:
    """Load rows from a comparison Excel file (0-based column indices)."""
    if not excel_path.is_file():
        print(f"ERROR: Excel file not found: {excel_path}")
        sys.exit(1)

    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    if worksheet not in wb.sheetnames:
        print(f"ERROR: Worksheet not found: {worksheet!r}. Available: {wb.sheetnames}")
        sys.exit(1)
    ws = wb[worksheet]

    submissions: list[dict[str, Any]] = []
    for row_idx, row in enumerate(
        ws.iter_rows(min_row=min_data_row, values_only=True),
        start=min_data_row,
    ):
        try:
            n = max(col_student, col_old_ai, col_human, col_answer) + 1
            r = list(row) + [None] * max(0, n - len(row))
            student_num = float(r[col_student]) if r[col_student] is not None else None
            ai_score_old = float(r[col_old_ai]) if r[col_old_ai] is not None else None
            human_val = r[col_human]
            human_score = float(human_val) if human_val is not None else None
            student_answer = str(r[col_answer]).strip() if r[col_answer] is not None else ""

            if (
                student_num
                and ai_score_old is not None
                and human_score is not None
                and student_answer
            ):
                submissions.append(
                    {
                        "student_id": int(student_num),
                        "ai_score_old": ai_score_old,
                        "human_score": human_score,
                        "student_answer": student_answer,
                        "row_idx": row_idx,
                    }
                )
        except (ValueError, TypeError, IndexError):
            pass

    return submissions


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Re-grade Quiz 1 Q13 with the few-shot-calibrated BPR prompt."
    )
    p.add_argument(
        "excel",
        type=Path,
        help="Path to the anonymized AI vs human comparison .xlsx",
    )
    p.add_argument(
        "--worksheet",
        default="Quiz 1 Anonymized Results",
        help="Worksheet name (default: %(default)s)",
    )
    p.add_argument(
        "--min-row",
        type=int,
        default=2,
        help="First data row in the sheet (default: 2, i.e. skip header in row 1)",
    )
    p.add_argument(
        "--col-student",
        type=int,
        default=0,
        metavar="I",
        help="0-based column for anonymized student id (default: 0)",
    )
    p.add_argument(
        "--col-old-ai",
        type=int,
        default=1,
        metavar="I",
        help="0-based column for prior AI score (default: 1)",
    )
    p.add_argument(
        "--col-human",
        type=int,
        default=2,
        metavar="I",
        help="0-based column for human score (default: 2)",
    )
    p.add_argument(
        "--col-answer",
        type=int,
        default=6,
        metavar="I",
        help="0-based column for student free-text answer (default: 6)",
    )
    p.add_argument(
        "--prompt-file",
        type=Path,
        default=_DEFAULT_PROMPT_FILE,
        help=f"System prompt with few-shot anchors (default: {_DEFAULT_PROMPT_FILE.name})",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write detailed results to this path (default: next to this script as quiz1_regrade_results.json)",
    )
    p.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write a JSON report",
    )
    return p.parse_args()


def grade_submission_with_new_prompt(
    student_answer: str,
    api_key: str,
    system_prompt: str,
    model: str,
) -> dict[str, Any] | None:
    client = openai.OpenAI(api_key=api_key)
    user_message = (
        "Grade the student's response to the question: "
        '"Why do we need to do Business Process Re-engineering '
        'as a part of implementing an EHR?"\n\n'
        f"STUDENT ANSWER:\n{student_answer}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=800,
        )
    except Exception as e:
        if is_nonrecoverable_api_error(e):
            print(f"\nNonrecoverable API error with model {model}: {e}")
            return None
        print(f"\nAPI Error: {e}")
        return None

    raw = (response.choices[0].message.content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    print(f"\nFailed to parse JSON from response:\n{raw[:200]}")
    return None


def extract_new_ai_score(grade_result: dict[str, Any]) -> float | None:
    if not grade_result:
        return None
    if "total_points" in grade_result:
        return float(grade_result["total_points"])
    if "criterion_scores" in grade_result:
        total = sum(
            float(c.get("awarded_points", 0)) for c in grade_result["criterion_scores"]
        )
        return total if total > 0 else None
    return None


def compute_confidence(grade_result: dict[str, Any]) -> float:
    if not grade_result or "criterion_scores" not in grade_result:
        return 0.0
    scores = {c["criterion_id"]: float(c.get("awarded_points", 0)) for c in grade_result["criterion_scores"]}
    a_score = min(scores.get("A", 0) / 8.0, 1.0)
    b_score = min(scores.get("B", 0) / 3.0, 1.0)
    c_score = min(scores.get("C", 0) / 2.0, 1.0)
    d_score = min(scores.get("D", 0) / 3.0, 1.0)
    confidence = a_score * 0.4 + b_score * 0.2 + c_score * 0.2 + d_score * 0.2
    total_points = (
        scores.get("A", 0) + scores.get("B", 0) + scores.get("C", 0) + scores.get("D", 0)
    )
    if total_points <= 6:
        confidence = min(confidence, 0.4)
    return max(0.0, min(confidence, 1.0))


def compute_review_risk(
    grade_result: dict[str, Any],
    old_ai: float,
    new_ai: float,
) -> float:
    if not grade_result:
        return 1.0
    confidence = compute_confidence(grade_result)
    delta = abs(new_ai - old_ai) / 8.0
    scores = {
        c["criterion_id"]: float(c.get("awarded_points", 0))
        for c in grade_result.get("criterion_scores", [])
    }
    total_points = scores.get("A", 0) + scores.get("B", 0) + scores.get("C", 0) + scores.get("D", 0)
    normalized_total = min(total_points / 16.0, 1.0)
    edge_risk = 0.2 if (new_ai <= 4 or new_ai >= 15) else 0.0
    risk = (1.0 - confidence) * 0.35 + delta * 0.35 + (1.0 - normalized_total) * 0.25 + edge_risk
    return max(0.0, min(risk, 1.0))


def needs_human_review(risk: float) -> bool:
    return risk >= 0.45


def main() -> None:
    args = parse_args()
    system_prompt = load_system_prompt(args.prompt_file)

    print("=" * 80)
    print("QUIZ 1 BPR GRADING (few-shot–calibrated prompt)")
    print("=" * 80)
    print("\nLoading Quiz 1 data...")

    submissions = load_quiz1_data(
        args.excel.resolve(),
        args.worksheet,
        args.col_student,
        args.col_old_ai,
        args.col_human,
        args.col_answer,
        args.min_row,
    )
    print(f"Loaded {len(submissions)} submissions\n")
    if not submissions:
        print("No valid rows. Check column indices and that scores/answers are present.")
        sys.exit(1)

    api_key = get_api_key()
    out_json = args.out_json
    if out_json is None and not args.no_json:
        out_json = Path(__file__).resolve().parent / "quiz1_regrade_results.json"

    print("Grading with new prompt (this may take a minute)...\n")
    print(
        f"{'Student':<8} {'Old AI':<8} {'New AI':<8} {'Human':<8} "
        f"{'Gap (Old)':<12} {'Gap (New)':<12} {'Risk':<6} {'Conf':<6} {'Review':<6}"
    )
    print("-" * 110)

    results: list[dict[str, Any]] = []
    old_gaps: list[float] = []
    new_gaps: list[float] = []

    for i, sub in enumerate(submissions, 1):
        student_id = sub["student_id"]
        old_ai = sub["ai_score_old"]
        human = sub["human_score"]
        answer = sub["student_answer"]

        grade_result = grade_submission_with_new_prompt(
            answer, api_key, system_prompt, args.model
        )
        new_ai = extract_new_ai_score(grade_result) or old_ai

        risk = compute_review_risk(grade_result, old_ai, new_ai) if grade_result else 1.0
        confidence = compute_confidence(grade_result) if grade_result else 0.0
        audit_flag = needs_human_review(risk)

        old_gap = old_ai - human
        new_gap = new_ai - human
        old_gaps.append(old_gap)
        new_gaps.append(new_gap)

        results.append(
            {
                "student_id": student_id,
                "ai_score_old": old_ai,
                "ai_score_new": new_ai,
                "human_score": human,
                "gap_old": old_gap,
                "gap_new": new_gap,
                "risk": risk,
                "confidence": confidence,
                "needs_human_review": audit_flag,
                "grade_result": grade_result,
            }
        )

        old_gap_str = f"{old_gap:+.1f}" if old_gap != 0 else "0.0"
        new_gap_str = f"{new_gap:+.1f}" if new_gap != 0 else "0.0"
        review_str = "YES" if audit_flag else "NO"
        print(
            f"{student_id:<8.0f} {old_ai:<8.0f} {new_ai:<8.1f} {human:<8.0f} "
            f"{old_gap_str:<12} {new_gap_str:<12} {risk:<6.2f} {confidence:<6.2f} {review_str:<6}"
        )
        if i % 5 == 0:
            print(f"  ... progress: {i}/{len(submissions)}")

    print("\n" + "=" * 80)
    print("CALIBRATION RESULTS")
    print("=" * 80)

    mean_old_gap = statistics.mean(old_gaps)
    mean_new_gap = statistics.mean(new_gaps)
    stdev_old_gap = statistics.stdev(old_gaps) if len(old_gaps) > 1 else 0.0
    stdev_new_gap = statistics.stdev(new_gaps) if len(new_gaps) > 1 else 0.0
    improvement = abs(mean_old_gap) - abs(mean_new_gap)
    improvement_pct = 100 * improvement / abs(mean_old_gap) if mean_old_gap != 0 else 0.0

    print(f"\nOLD PROMPT (AI vs Human):")
    print(
        f"  Mean gap: {mean_old_gap:.2f} (AI tends to "
        f"{'underscore' if mean_old_gap < 0 else 'overscore'})"
    )
    print(f"  Std dev:  {stdev_old_gap:.2f}")
    print(f"  Range:    {min(old_gaps):.1f} to {max(old_gaps):.1f}")

    print(f"\nNEW PROMPT (AI vs Human):")
    print(
        f"  Mean gap: {mean_new_gap:.2f} (AI tends to "
        f"{'underscore' if mean_new_gap < 0 else 'overscore'})"
    )
    print(f"  Std dev:  {stdev_new_gap:.2f}")
    print(f"  Range:    {min(new_gaps):.1f} to {max(new_gaps):.1f}")

    print(f"\nIMPROVEMENT:")
    print(f"  Bias reduced by:     {improvement:.2f} points ({improvement_pct:.1f}%)")
    print(f"  Variance reduced by: {stdev_old_gap - stdev_new_gap:.2f} points")

    if abs(mean_new_gap) < 0.5:
        print(f"\nNew mean |gap| from human: {abs(mean_new_gap):.2f} (target example: < 0.5)")
    else:
        print(f"\nMean gap remains: {abs(mean_new_gap):.2f} (tune prompt or review sheet layout)")

    if not args.no_json and out_json is not None:
        payload = {
            "summary": {
                "total_submissions": len(submissions),
                "mean_gap_old": mean_old_gap,
                "mean_gap_new": mean_new_gap,
                "improvement": improvement,
                "improvement_pct": improvement_pct,
            },
            "results": results,
        }
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nDetailed results saved to: {out_json}")


if __name__ == "__main__":
    main()
