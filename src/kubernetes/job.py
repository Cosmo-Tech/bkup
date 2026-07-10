import yaml
import time
from pathlib import Path
from kubernetes import utils, client
from kubernetes.client.rest import ApiException


def get_job_name(kube, pvc_name: str) -> str:
    """Automatic name for backup jobs (63 chars limited)"""
    return f"backup-{pvc_name}"[-63:].lower().strip("-")


def create_backup_job(kube, pvc_name: str, mounted_path: str, node_name: str) -> bool:
    """Create a Kubernetes job to run backups"""

    cluster_name = kube.get_cluster_name()
    namespace = kube.get_namespace()

    secret_required = "clustersnap-config"
    if not kube.check_secret_exists(secret_required):
        print(f"error: Secret '{secret_required}' is missing in namespace '{namespace}'. Cancelling backup job.")
        return False

    job_name = kube.get_job_name(pvc_name)
    print(f"starting backup job: {job_name}")

    project_root = Path(__file__).resolve().parents[2]
    template_path = f"{project_root}/templates/backup.job.yaml"
    try:
        with open(template_path, "r") as f:
            yaml_content = f.read()

        yaml_content = yaml_content.replace("{{ NAMESPACE }}", namespace)
        yaml_content = yaml_content.replace("{{ JOB_NAME }}", job_name)
        yaml_content = yaml_content.replace("{{ PVC_NAME }}", pvc_name)
        yaml_content = yaml_content.replace("{{ MOUNTED_PATH }}", mounted_path)
        yaml_content = yaml_content.replace("{{ NODE_NAME }}", node_name)

        job_dict = yaml.safe_load(yaml_content)

        if "metadata" not in job_dict:
            job_dict["metadata"] = {}
        job_dict["metadata"]["namespace"] = namespace

        try:
            containers = job_dict["spec"]["template"]["spec"]["containers"]
            for container in containers:
                if "env" not in container or container["env"] is None:
                    container["env"] = []
                container["env"].append({
                    "name": "CLUSTER_NAME",
                    "value": str(cluster_name)
                })
        except KeyError as e:
            print(f"warn: {e}")

        utils.create_from_dict(kube.client_corev1.api_client, data=job_dict)
        return True
    except Exception as e:
        print(f"error: {e}")
        return False


def delete_backup_job(kube, pvc_name: str) -> bool:
    """Delete a Kubernetes backup job"""

    namespace = kube.get_namespace()

    job_name = kube.get_job_name(pvc_name)
    try:
        kube.client_batchv1.delete_namespaced_job(name=job_name, namespace=namespace, propagation_policy="Background")
        print(f"  Old job '{job_name}' successfully removed.")
        return True
    except ApiException as e:
        if e.status == 404:
            return True
        print(f"error: deleting job {job_name}: {e}")
        return False


def wait_for_jobs_deletion(kube, pvc_list: list) -> bool:
    """Hook to wait for a job to be deleted"""

    namespace = kube.get_namespace()

    jobs_to_wait = [get_job_name(kube, pvc) for pvc in pvc_list]
    print(f"  Waiting for {len(jobs_to_wait)} old jobs to be fully purged from cluster...", end="", flush=True)
    
    start_time = time.time()
    timeout = 60
    while time.time() - start_time < timeout:
        active_jobs = []
        for job_name in jobs_to_wait:
            try:
                kube.client_batchv1.read_namespaced_job(name=job_name, namespace=namespace)
                active_jobs.append(job_name)
            except ApiException as e:
                if e.status == 404:
                    continue
                else:
                    print(f"\n  error: checking job {job_name}: {e}")
                    return False

        if not active_jobs:
            print(" Done!")
            return True
        print(".", end="", flush=True)
        time.sleep(2)
    print("\n  error: timeout reached while waiting for old jobs deletion.")
    return False


def wait_for_job_completion(kube, job_name: str) -> bool:
    """Blocks the execution until the Kubernetes Job is either Succeeded or Failed."""

    print(f"info: waiting for job '{job_name}' to complete...", flush=True)
    start_time = time.time()
    while True:
        timeout = 1800
        if (time.time() - start_time) > timeout:
            print(f"error: timeout reached ({timeout}s) while waiting for job '{job_name}'.")
            return False
        try:
            job = kube.client_batchv1.read_namespaced_job_status(name=job_name, namespace=kube.get_namespace())
            status = job.status

            if status.succeeded and status.succeeded > 0:
                print(f"info: job '{job_name}' completed successfully.", flush=True)
                return True

            if status.failed and status.failed > 0:
                print(f"error: job '{job_name}' failed during execution.", flush=True)
                return False

            print(f"info: job is still running (active pods: {status.active or 0}). checking again in 10s...", flush=True)
            time.sleep(10)
        except ApiException as e:
            print(f"error: failed to read job status: {e}", flush=True)
            time.sleep(10)
