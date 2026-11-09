"""Offline regression check for the pinned MLflow/SQLite dependencies."""

from pathlib import Path


def test_mlflow_sqlite_storage_survives_reopening(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
    from mlflow.entities import Param
    from mlflow.store.artifact.local_artifact_repo import LocalArtifactRepository
    from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore

    database = tmp_path / "mlflow.db"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    database_uri = "sqlite:///" + database.as_posix()
    store = SqlAlchemyStore(database_uri, artifacts.as_uri())
    try:
        experiment_id = store.create_experiment("stage1-infrastructure-smoke")
        run = store.create_run(
            experiment_id, user_id="smoke", start_time=0, tags=[],
            run_name="storage-check",
        )
        store.log_param(run.info.run_id, Param("purpose", "infrastructure-smoke"))
        source = tmp_path / "storage-check.txt"
        source.write_text("synthetic infrastructure artifact", encoding="utf-8")
        repository = LocalArtifactRepository(run.info.artifact_uri)
        repository.log_artifact(str(source))
        assert database.stat().st_size > 0
    finally:
        store.engine.dispose()

    reopened = SqlAlchemyStore(database_uri, artifacts.as_uri())
    try:
        assert reopened.get_experiment(experiment_id).name == "stage1-infrastructure-smoke"
        restored_run = reopened.get_run(run.info.run_id)
        assert restored_run.data.params == {"purpose": "infrastructure-smoke"}
        restored_repository = LocalArtifactRepository(restored_run.info.artifact_uri)
        download_dir = tmp_path / "download"
        download_dir.mkdir()
        downloaded = restored_repository.download_artifacts(
            "storage-check.txt", dst_path=str(download_dir)
        )
        assert Path(downloaded).read_text(encoding="utf-8") == (
            "synthetic infrastructure artifact"
        )
    finally:
        reopened.engine.dispose()
