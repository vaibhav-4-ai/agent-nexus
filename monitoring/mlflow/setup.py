"""MLflow setup script for DagsHub integration."""

import os

def setup_mlflow():
    """Initialize MLflow experiment on DagsHub."""
    tracking_uri = os.environ.get("MONITORING_MLFLOW_TRACKING_URI", "")
    if not tracking_uri:
        print("MONITORING_MLFLOW_TRACKING_URI not set. Skipping MLflow setup.")
        return

    try:
        import mlflow
        dagshub_token = os.environ.get("MONITORING_DAGSHUB_TOKEN", "")
        if dagshub_token:
            os.environ["MLFLOW_TRACKING_USERNAME"] = dagshub_token
            os.environ["MLFLOW_TRACKING_PASSWORD"] = dagshub_token

        mlflow.set_tracking_uri(tracking_uri)
        experiment_name = os.environ.get("MONITORING_MLFLOW_EXPERIMENT_NAME", "agent-nexus")
        mlflow.set_experiment(experiment_name)
        print(f"MLflow configured: {tracking_uri} / {experiment_name}")

        # Log a test metric
        with mlflow.start_run(run_name="setup-test"):
            mlflow.log_param("setup", "complete")
            mlflow.log_metric("test_metric", 1.0)
        print("MLflow test run logged successfully!")
    except Exception as e:
        print(f"MLflow setup failed: {e}")

if __name__ == "__main__":
    setup_mlflow()
