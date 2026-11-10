"""Offline audit of saved benchmark artifacts. Never constructs a provider or MLflow client."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from backend.llm.analyzer import extract_fragments, grounded_findings
from backend.llm.schemas import SemanticIssue
from evaluation.dataset import build_case, dataset_hash, load_dataset
from evaluation.metrics import LEGACY_MATCHING_VERSION, MATCHING_VERSION, aggregate, score_events


def analyze_saved_run(directory: Path) -> dict:
    paths = [directory / name for name in ("summary.json", "cases.json", "manifest.json")]
    summary, rows, manifest = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    params = summary["params"]
    if manifest["params"] != params or params["matching_version"] != LEGACY_MATCHING_VERSION:
        raise ValueError("Expected a consistent v1 baseline")
    dataset = load_dataset()
    if dataset_hash(dataset) != params["dataset_sha256"]:
        raise ValueError("Dataset hash differs; do not silently rescore against changed labels")
    source = {c.document_id: c for c in dataset.cases}
    saved = {c["document_id"]: c for c in manifest["cases"]}
    ids = [r["document_id"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(source) or len(saved) != len(manifest["cases"]) or set(saved) != set(source):
        raise ValueError("Missing or duplicate case IDs")
    rescored = copy.deepcopy(rows)
    changes = []
    for row in rescored:
        case = source[row["document_id"]]
        _, parsed, gold = build_case(case)
        if saved[case.document_id]["gold"] != [g.model_dump() for g in gold] or saved[case.document_id]["paragraphs"] != case.paragraphs:
            raise ValueError("Saved manifest differs from the versioned dataset")
        if row["kind"] != case.kind or row["gold_count"] != len(gold):
            raise ValueError("Saved case metadata differs from ground truth")
        predictions = [SemanticIssue(error_type="contradiction", references=refs,
                       explanation="Offline reconstruction of saved accepted citations.",
                       suggestion="Review source facts.") for refs in row["prediction_evidence"]]
        if len(predictions) != row["prediction_count"] or (predictions and row["status"] != "completed"):
            raise ValueError("Inconsistent accepted predictions")
        if row["prediction_locations"] != [[r.element_id for r in p.references] for p in predictions]:
            raise ValueError("Saved locations differ from saved evidence")
        # Revalidate saved accepted quotes against source; never guess rejected quotes.
        grounded_findings(json.dumps({"issues": [p.model_dump() for p in predictions]}, ensure_ascii=False),
                          extract_fragments(parsed), parsed.sha256)
        baseline = score_events(predictions, gold, matching_version=LEGACY_MATCHING_VERSION)
        if any(row[key] != baseline[key] for key in ("tp", "fp", "fn")):
            raise ValueError("Saved counts do not reproduce under the v1 matcher")
        corrected = score_events(predictions, gold)
        if any(baseline[key] != corrected[key] for key in ("tp", "fp", "fn")):
            changes.append({"document_id": case.document_id, "baseline": baseline, "rescored": corrected})
        row.update({key: corrected[key] for key in ("tp", "fp", "fn")})
    baseline_metrics = aggregate(rows)
    for key, value in summary["metrics"].items():
        if key in baseline_metrics and baseline_metrics[key] != value:
            raise ValueError(f"Saved summary does not reproduce: {key}")
    return {"source_run": directory.name, "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "mode": "offline-rescore-no-model-calls", "baseline_params": params,
            "baseline_metrics": baseline_metrics, "rescored_matching_version": MATCHING_VERSION,
            "rescored_metrics": aggregate(rescored), "changed_cases": changes,
            "evidence_failures": [r["document_id"] for r in rows if r["error_code"] == "invalid_llm_evidence"],
            "limitation": "Rejected quotes and explanations were not saved; exact historical quote defects cannot be recovered."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file outside the source run")
    args = parser.parse_args(argv)
    source = args.run_dir.resolve()
    target = args.output.resolve()
    if target == source or source in target.parents:
        parser.error("Output must be outside the immutable source run")
    result = analyze_saved_run(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Offline audit saved: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
