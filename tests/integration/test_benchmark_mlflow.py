"""Real MLflow client + SQLite + artifact round trip, entirely offline."""

import json

from backend.core.config import Settings
from evaluation.benchmark import run_benchmark


def test_mock_benchmark_persists_mlflow_metrics_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
    from mlflow import MlflowClient

    uri = "sqlite:///" + (tmp_path / "tracking.db").as_posix()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    client = MlflowClient(tracking_uri=uri)
    client.create_experiment("semantic-benchmark-mock", artifact_location=artifacts.as_uri())
    directory, success = run_benchmark(Settings(_env_file=None, mlflow_tracking_uri=uri),
        provider=None, dry_run=True, allow_paid=False, output=tmp_path / "results", track=True)
    assert success
    run_id = json.loads((directory / "tracking.json").read_text())["run_id"]
    reopened = MlflowClient(tracking_uri=uri)
    run = reopened.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.params["mode"] == "mock" and run.data.params["provider"] == "mock"
    assert run.data.metrics["fn"] == 12 and run.data.metrics["case_count"] == 24
    assert run.data.metrics["issue_count_observed_case_count"] == 24
    assert run.data.metrics["successful_empty_case_count"] == 24
    assert run.data.metrics["validation_error_count"] == 0
    assert {a.path for a in reopened.list_artifacts(run_id)} == {"summary.json", "cases.json", "cases.csv", "manifest.json"}
    path = reopened.download_artifacts(run_id, "summary.json", str(tmp_path))
    assert json.loads(open(path, encoding="utf-8").read())["metrics"]["fn"] == 12
