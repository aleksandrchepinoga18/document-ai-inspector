"""Replay a frozen, synthetic real-run artifact without LLM or tracking calls."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from evaluation.analyze_run import analyze_saved_run, main


FIXTURE = Path(__file__).parents[1] / "fixtures" / "ollama-stage5-baseline"


def hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}


def test_frozen_baseline_reproduces_and_only_storage_changes(monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Offline audit must not send requests"))
    before = hashes(FIXTURE)
    result = analyze_saved_run(FIXTURE)
    assert hashes(FIXTURE) == before
    assert result["baseline_metrics"]["tp"] == result["baseline_metrics"]["fn"] * 2 == 8
    assert result["baseline_metrics"]["f1"] == pytest.approx(2 / 3)
    assert result["rescored_metrics"]["tp"] == 9
    assert result["rescored_metrics"]["fp"] == result["rescored_metrics"]["fn"] == 3
    assert result["rescored_metrics"]["f1"] == .75
    assert [r["document_id"] for r in result["changed_cases"]] == ["positive-storage"]
    assert set(result["evidence_failures"]) == {"positive-retention", "positive-warranty", "positive-staff",
                                              "negative-same", "negative-history"}
    assert result["baseline_metrics"]["negative_validation_error_count"] == 2
    assert result["baseline_metrics"]["positive_validation_error_count"] == 3
    assert result["baseline_metrics"]["issue_count_observed_case_count"] == 0


def test_offline_cli_writes_separate_output_and_refuses_source_overwrite(tmp_path):
    output = tmp_path / "audit.json"
    assert main(["--run-dir", str(FIXTURE), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "offline-rescore-no-model-calls"
    with pytest.raises(SystemExit):
        main(["--run-dir", str(FIXTURE), "--output", str(FIXTURE / "summary.json")])
    with pytest.raises(FileExistsError):
        main(["--run-dir", str(FIXTURE), "--output", str(output)])
