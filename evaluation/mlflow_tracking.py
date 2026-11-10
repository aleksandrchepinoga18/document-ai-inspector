"""Explicit tracking sink; only benchmark-owned, allowlisted files are uploaded."""

from pathlib import Path


def log_benchmark(directory: Path, params: dict, metrics: dict, tracking_uri: str) -> str:
    # Lazy import: normal document checking never depends on tracking availability.
    from mlflow import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    name = "semantic-benchmark-mock" if params["mode"] == "mock" else "semantic-benchmark"
    experiment = client.get_experiment_by_name(name)
    experiment_id = experiment.experiment_id if experiment else client.create_experiment(name)
    run = client.create_run(experiment_id, tags={"benchmark.mode": params["mode"],
                                               "benchmark.synthetic_only": "true"},
                            run_name=f'{params["provider"]}-{directory.name}')
    run_id = run.info.run_id
    try:
        for key, value in params.items():
            client.log_param(run_id, key, str(value))
        for key, value in metrics.items():
            client.log_metric(run_id, key, float(value))
        for name in ("summary.json", "cases.json", "cases.csv", "manifest.json"):
            client.log_artifact(run_id, str(directory / name))
        client.set_terminated(run_id, "FINISHED")
    except Exception:
        client.set_terminated(run_id, "FAILED")
        raise
    return run_id
