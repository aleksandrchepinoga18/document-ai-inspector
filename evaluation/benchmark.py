"""Manual, synthetic-only LLM benchmark. No live requests without explicit selection."""

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import httpx

from backend.core.config import Settings
from backend.llm.analyzer import analyze_document
from backend.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from backend.llm.providers import create_provider
from backend.llm.runner import run_llm_analysis
from backend.llm.schemas import SemanticIssue, SemanticResponse
from evaluation.dataset import GENERATOR_VERSION, build_case, dataset_hash, load_dataset
from evaluation.metrics import MATCHING_VERSION, METRICS_VERSION, aggregate, score_events
from evaluation.mlflow_tracking import log_benchmark


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mock_reply(request):
    # Empty-response baseline, deliberately independent of the gold labels.
    return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
                                   "model": "mock-empty", "choices": [{"index": 0,
                                   "finish_reason": "stop", "message": {"role": "assistant",
                                   "content": '{"issues":[]}'}}]})


def run_benchmark(settings: Settings, *, provider: str | None, dry_run: bool,
                  allow_paid: bool, output: Path, track: bool) -> tuple[Path, bool]:
    if dry_run and (provider or allow_paid):
        raise ValueError("--dry-run cannot select a real provider or --allow-paid")
    if not dry_run and not provider:
        raise ValueError("Select --provider explicitly, or use --dry-run")
    if provider == "yandex" and not allow_paid:
        raise ValueError("Yandex requires explicit --allow-paid")
    if allow_paid and provider != "yandex":
        raise ValueError("--allow-paid is only valid with --provider yandex")
    dataset = load_dataset()
    if dry_run:
        settings = Settings(_env_file=None, **{**settings.model_dump(), "ollama_model": "mock-empty",
                            "ollama_base_url": "http://localhost:11434/v1",
                            "ollama_api_key": "", "llm_max_retries": 0})
        model = "mock-empty"
    else:
        model = create_provider(provider, settings, allow_cloud=allow_paid, synthetic_data=True).model
    params = {"mode": "mock" if dry_run else "real", "provider": "mock" if dry_run else provider,
              "model": model, "dataset_version": dataset.version, "dataset_sha256": dataset_hash(dataset),
              "generator_version": GENERATOR_VERSION, "prompt_version": PROMPT_VERSION,
              "prompt_sha256": hashlib.sha256((SYSTEM_PROMPT + json.dumps(SemanticResponse.model_json_schema(), sort_keys=True)).encode()).hexdigest(),
              "matching_version": MATCHING_VERSION, "metrics_version": METRICS_VERSION,
              "validation_diagnostics_version": "first-failure-coordinates-v1",
              "ambiguous_policy": "exclude-from-quality-count-separately",
              "latency_scope": "worker-wall-time" if not dry_run else "mock-analyzer",
              "python_version": sys.version.split()[0],
              **{f"version_{package}": version(package) for package in ("python-docx", "lxml", "pydantic", "openai", "mlflow")},
              **{k: v for k, v in settings.model_dump().items() if k.startswith("llm_")}}
    # Never persist endpoint URLs, credentials, free-form model replies or input DOCX.
    secrets = [getattr(settings, p + "_api_key").get_secret_value() for p in ("ollama", "vllm", "yandex")]
    if any(secret and secret in json.dumps(params) for secret in secrets):
        raise ValueError("Sensitive value in benchmark configuration")
    directory = output / f'{params["mode"]}-{uuid4().hex}'
    directory.mkdir(parents=True, exist_ok=False)
    rows, manifest = [], []
    with tempfile.TemporaryDirectory(prefix="inspector-benchmark-") as temporary:
        for case in dataset.cases:
            data, parsed, gold = build_case(case)
            manifest.append({"document_id": case.document_id, "docx_sha256": parsed.sha256,
                             "kind": case.kind, "paragraphs": case.paragraphs, "rationale": case.rationale,
                             "gold": [e.model_dump() for e in gold]})
            if dry_run:
                findings, metadata = analyze_document(parsed, settings, "ollama", transport=httpx.MockTransport(mock_reply))
            else:
                path = Path(temporary) / "synthetic.docx"
                path.write_bytes(data)
                findings, metadata = run_llm_analysis(path, settings, provider, allow_cloud=allow_paid,
                                                      synthetic_data=True, timeout=settings.llm_total_timeout_seconds)
            predictions = [SemanticIssue(error_type="contradiction", references=f.actual["claims"],
                                         explanation=f.actual["explanation"], suggestion=f.suggestion,
                                         severity=f.severity) for f in findings]
            counts = score_events(predictions, gold)
            rows.append({"document_id": case.document_id, "kind": case.kind,
                         "prediction_count": len(predictions), "gold_count": len(gold),
                         "tp": counts["tp"], "fp": counts["fp"], "fn": counts["fn"],
                         "prediction_locations": [[r.element_id for r in p.references] for p in predictions],
                         "prediction_evidence": [[r.model_dump() for r in p.references] for p in predictions],
                         "validation_diagnostics": [d.model_dump() for d in metadata.validation_diagnostics],
                         **{k: getattr(metadata, k) for k in ("status", "json_valid", "references_valid",
                                                             "duration_seconds", "error_code", "attempts",
                                                             "response_issue_count", "discarded_issue_count")}})
    metrics = aggregate(rows)
    write_json(directory / "summary.json", {"params": params, "metrics": metrics,
               "limitation": "Synthetic-only; mock runs do not measure real models. Not evidence of production quality."})
    write_json(directory / "cases.json", rows)
    write_json(directory / "manifest.json", {"params": params, "cases": manifest})
    with (directory / "cases.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    tracking_ok = True
    if track:
        try:
            run_id = log_benchmark(directory, params, metrics, settings.mlflow_tracking_uri)
            write_json(directory / "tracking.json", {"status": "completed", "run_id": run_id})
        except Exception:
            # Raw exceptions can contain URLs/auth data. Keep local results for diagnosis.
            tracking_ok = False
            write_json(directory / "tracking.json", {"status": "failed", "error_code": "mlflow_logging_failed"})
    return directory, tracking_ok and metrics["failed_case_count"] == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--provider", choices=["ollama", "vllm", "yandex"])
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--no-mlflow", action="store_true", help="Save local artifacts only")
    parser.add_argument("--output", type=Path, default=Path("results/benchmarks"))
    args = parser.parse_args(argv)
    if args.provider == "yandex" and not args.allow_paid:
        parser.error("--provider yandex requires explicit --allow-paid (24 synthetic cases)")
    if args.allow_paid and args.provider != "yandex":
        parser.error("--allow-paid requires --provider yandex")
    try:
        directory, success = run_benchmark(Settings(), provider=args.provider, dry_run=args.dry_run,
                                          allow_paid=args.allow_paid, output=args.output, track=not args.no_mlflow)
    except Exception:
        print("Benchmark refused or failed. Check provider configuration, consent flags and output permissions; no credentials logged.", file=sys.stderr)
        return 2
    print(f"{'MOCK (no real model)' if args.dry_run else 'REAL synthetic benchmark'}: {directory}")
    if not success:
        print("Incomplete run: inspect summary.json, cases.json and tracking.json.", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
