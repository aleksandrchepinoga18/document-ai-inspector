"""Offline CLI, corpus reproducibility, consent and failure-path regression tests."""

import json

import pytest
import httpx

from backend.core.config import Settings
from backend.core.schemas import LLMAnalysis
from backend.llm.analyzer import extract_fragments, grounded_findings
from backend.llm.analyzer import analyze_document
from backend.documents.parser import parse_document
from evaluation import benchmark
from evaluation.dataset import build_case, dataset_hash, load_dataset


def test_dataset_is_synthetic_grounded_and_reproducible():
    dataset = load_dataset()
    assert len(dataset.cases) == 24
    assert {kind: sum(c.kind == kind for c in dataset.cases) for kind in ("positive", "negative", "ambiguous")} == {
        "positive": 12, "negative": 8, "ambiguous": 4}
    assert dataset_hash(dataset) == dataset_hash(load_dataset())
    for case in dataset.cases:
        data, parsed, gold = build_case(case)
        assert data == build_case(case)[0]
        assert "?" not in "".join(case.paragraphs)
        payload = {"issues": [dict(error_type=e.error_type, references=[r.model_dump() for r in e.references],
                       explanation="Incompatible claims about the same subject.", suggestion="Review the source.") for e in gold]}
        assert len(grounded_findings(json.dumps(payload), extract_fragments(parsed), parsed.sha256)) == len(gold)


@pytest.mark.parametrize("args", [[], ["--provider", "yandex"], ["--dry-run", "--allow-paid"],
                                  ["--provider", "ollama", "--allow-paid"], ["--dry-run", "--provider", "yandex"]])
def test_cli_rejects_implicit_or_unapproved_requests(tmp_path, monkeypatch, args):
    def forbidden(*a, **kw):
        pytest.fail("Must reject before network/model selection")
    monkeypatch.setattr(benchmark, "create_provider", forbidden)
    monkeypatch.setattr(benchmark, "run_llm_analysis", forbidden)
    try:
        code = benchmark.main([*args, "--output", str(tmp_path)])
    except SystemExit as exc:
        code = exc.code
    assert code == 2 and not list(tmp_path.iterdir())


def test_dry_run_uses_mock_and_writes_inspectable_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(benchmark, "run_llm_analysis", lambda *a, **k: pytest.fail("No real worker"))
    assert benchmark.main(["--dry-run", "--no-mlflow", "--output", str(tmp_path)]) == 0
    assert "MOCK" in capsys.readouterr().out
    result = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert result["params"]["provider"] == "mock" and result["params"]["model"] == "mock-empty"
    assert result["metrics"]["fn"] == 12 and result["metrics"]["tp"] == 0
    assert result["metrics"]["json_valid_rate"] == 1
    assert next(tmp_path.glob("*/cases.csv")).is_file()


def test_failed_model_cases_remain_in_recall_and_tracking_failure_preserves_results(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "run_llm_analysis", lambda *a, **k: ([], LLMAnalysis(
        status="failed", provider="ollama", error_code="llm_unavailable", attempts=1)))
    def tracking_failure(*a):
        raise RuntimeError("sensitive endpoint must not be printed")
    monkeypatch.setattr(benchmark, "log_benchmark", tracking_failure)
    directory, success = benchmark.run_benchmark(Settings(_env_file=None, ollama_model="test"),
        provider="ollama", dry_run=False, allow_paid=False, output=tmp_path, track=True)
    assert not success
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["metrics"]["fn"] == 12
    assert summary["metrics"]["api_error_count"] == 24
    assert json.loads((directory / "tracking.json").read_text())["status"] == "failed"
    assert "sensitive endpoint" not in "".join(f.read_text(encoding="utf-8") for f in directory.iterdir())


def test_scoring_accepts_grounded_predictions_and_counts_negative_false_positives(tmp_path, monkeypatch):
    from conftest import mock_chat_completion

    settings = Settings(_env_file=None, ollama_model="test-model", ollama_api_key="private-test-key")

    def offline_worker(path, settings, provider, **kwargs):
        parsed = parse_document(path.read_bytes())
        facts = [f for f in extract_fragments(parsed) if not f.text.startswith("Раздел ")]
        payload = {"issues": [{"error_type": "contradiction",
            "references": [{"element_id": f.element_id, "quote": f.text} for f in facts],
            "explanation": "Synthetic test deliberately reports every pair.", "suggestion": "Review facts."}]}
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=mock_chat_completion(json.dumps(payload))))
        return analyze_document(parsed, settings, provider, transport=transport)

    monkeypatch.setattr(benchmark, "run_llm_analysis", offline_worker)
    directory, success = benchmark.run_benchmark(settings, provider="ollama", dry_run=False,
        allow_paid=False, output=tmp_path, track=False)
    assert success
    metrics = json.loads((directory / "summary.json").read_text())["metrics"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"], metrics["ambiguous_predictions"]) == (12, 8, 0, 4)
    artifacts = "".join(f.read_text(encoding="utf-8") for f in directory.iterdir())
    assert "private-test-key" not in artifacts and "http://" not in artifacts
