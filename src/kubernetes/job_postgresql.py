import yaml
from kubernetes import utils, client


def get_workload_image(self, resource_type: str, resource_name: str) -> str:
    """Query the Kubernetes API to find the image tag used by production."""
    try:
        if resource_type.lower() == "statefulset":
            # Assumes self.apps_v1 is initialized as client.AppsV1Api()
            sts = self.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=self.namespace)
            return sts.spec.template.spec.containers[0].image
        elif resource_type.lower() == "deployment":
            deploy = self.apps_v1.read_namespaced_deployment(name=resource_name, namespace=self.namespace)
            return deploy.spec.template.spec.containers[0].image
        else:
            raise ValueError(f"Unsupported workload type for dynamic image detection: {resource_type}")
    except Exception as e:
        print(f"  warn: Failed to dynamically detect image for {resource_name}, falling back to default postgres:latest. Error: {e}")
        return "postgres:latest"

def create_postgres_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, postgres_image: str, python_code: str):
    """Load the postgres job template, replace variables, and deploy to K8s control plane."""
    import yaml

    template_path = "templates/postgres_exporter.job.yaml"
    with open(template_path, "r") as f:
        template_content = f.read()

    job_name = f"clustersnap-psql-dump-{pvc_name}"

    # Map current workflow values into template placeholders
    rendered = template_content\
        .replace("{{ JOB_NAME }}", job_name)\
        .replace("{{ NAMESPACE }}", self.namespace)\
        .replace("{{ NODE_NAME }}", node_name)\
        .replace("{{ POSTGRES_IMAGE }}", postgres_image)\
        .replace("{{ MOUNTED_PATH }}", mounted_path)\
        .replace("{{ PYTHON_BACKUP_CODE }}", python_code)\
        .replace("{{ PVC_NAME }}", pvc_name)

    job_manifest = yaml.safe_load(rendered)

    # Assumes self.batch_v1 is initialized as client.BatchV1Api()
    try:
        self.batch_v1.create_namespaced_job(namespace=self.namespace, body=job_manifest)
        print(f"  info: Dedicated Postgres backup job '{job_name}' successfully triggered.")
    except Exception as e:
        print(f"  error: Failed to create namespaced job '{job_name}': {e}")
        raise e