import os
import time
import yaml
import jinja2
from pathlib import Path

from kubernetes import client, config
from src.utils import general as utils


class Kubernetes:
    def __init__(self, context: str = None):
        """Init Kubernetes connexion."""
        is_in_cluster = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")

        # Ensure not running on default context in case of CLI usage
        if not context and not is_in_cluster:
            raise ValueError("missing cluster context")

        self.context = context

        try:
            if is_in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(context=self.context)
        except Exception as e:
            raise RuntimeError(f"error: {utils.current_function()}: failed to initialize Kubernetes connection with context '{self.context}': {e}")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        # self.storage_v1 = client.StorageV1Api()


    def list_namespaces(self) -> list:
        results = []
        try:
            namespaces = self.core_v1.list_namespace()
            for i in namespaces.items:
                result = i.metadata.name
                results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list namespaces: {e}")
        return results


    def list_deployments(self, namespace: str) -> list:
        try:
            deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
            results = []
            for i in deployments.items:
                result = i
                # result = i.metadata.name
                results.append(result)
            return results
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list deployments: {e}")
            return []


    def list_statefulsets(self, namespace: str) -> list:
        try:
            statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace=namespace)
            results = []
            for i in statefulsets.items:
                result = i
                # result = i.metadata.name
                results.append(result)
            return results
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list statefulsets: {e}")
            return []


    def list_pvc(self, namespace: str) -> list:
        """List all PVC of a namespace"""
        results = []
        try:
            pvc_list = self.core_v1.list_namespaced_persistent_volume_claim(namespace=namespace)
            for i in pvc_list.items:
                result = i.metadata.name
                results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: {e}")
        return results



    def map_mountpoints_pvc_workload(self, pvc_name: str, workload: str) -> list:
        """Get mounted paths from combination of PVC name + workload manifest (= deployment, statefulset)"""
        results = []
        try:
            v_name = None
            w_name = workload.metadata.name
            w_spec = workload.spec.template.spec

            w_kind = getattr(workload, "kind", None)
            if not w_kind:
                # Get kind from class name "V1Deployment", "V1StatefulSet"
                class_name = workload.__class__.__name__
                w_kind = class_name.replace("V1", "")

            if not w_spec.volumes:
                return results

            # 1. Find the volume that is using to the PVC
            for v in w_spec.volumes:
                if v.persistent_volume_claim:
                    pvc = v.persistent_volume_claim.claim_name
                    if pvc == pvc_name:
                        v_name = v.name

            # 2. Find the mountpoints of the volume found before
            for c in w_spec.containers:
                for vm in c.volume_mounts:
                    if v_name == vm.name:
                        result = {
                            "pvc":              pvc_name,
                            "workload_kind":    w_kind,
                            "workload_name":    w_name,
                            "container":        c.name,
                            "volume":           vm.name,
                            "mount_path":       vm.mount_path
                            }
                        results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to retrieve workload/pvc/mountpoints combination: {e}")
        return results


    # def create_job(self,
    #     template_path: str,
    #     namespace: str,
    #     image: str,
    #     image_pull_policy: str,
    #     job_name: str,
    #     storage_protocol: str,
    #     storage_endpoint: str,
    #     storage_root: str,
    #     storage_username: str,
    #     storage_password: str,
    #     archive_name: str,
    #     # node_name: str,
    #     pvc_name: str,
    #     mount_path: str,
    #     ):
    #     """Create a Kubernetes job from a given manifest."""
    #     raw_yaml = Path(template_path).read_text(encoding="utf-8")
    #     rendered_yaml = jinja2.Template(raw_yaml).render(
    #         NAMESPACE=namespace,
    #         IMAGE=image,
    #         IMAGE_PULL_POLICY=image_pull_policy,
    #         JOB_NAME=job_name,
    #         STORAGE_PROTOCOL=storage_protocol,
    #         STORAGE_ENDPOINT=storage_endpoint,
    #         STORAGE_ROOT=storage_root,
    #         STORAGE_USERNAME=storage_username,
    #         STORAGE_PASSWORD=storage_password,
    #         ARCHIVE_NAME=archive_name,
    #         # NODE_NAME=node_name,
    #         PVC_NAME=pvc_name,
    #         MOUNT_PATH=mount_path,
    #         )

    #     body = yaml.safe_load(rendered_yaml)

    #     try:
    #         print(f"starting job {namespace}/{job_name}...")
    #         self.batch_v1.create_namespaced_job(namespace=namespace, body=body)
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to create job {namespace}/{job_name}: {e}")


    def create_job(self, job_config: dict):
        """Create a Kubernetes job from a template and configuration dictionary."""
        template_path   = job_config["TEMPLATE_PATH"]
        namespace       = job_config["NAMESPACE"]
        job_name        = job_config["JOB_NAME"]

        raw_yaml = Path(template_path).read_text(encoding="utf-8")
        rendered_yaml = jinja2.Template(raw_yaml).render(**job_config)
        body = yaml.safe_load(rendered_yaml)

        try:
            print(f"starting job {namespace}/{job_name}...")
            self.batch_v1.create_namespaced_job(namespace=namespace, body=body)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to create job {namespace}/{job_name}: {e}")

    # def _create_job(self, template_path: str, namespace: str, job_name: str, context: dict):
    #     """Base of the backup job"""
    #     raw_yaml = Path(template_path).read_text(encoding="utf-8")
    #     rendered_yaml = jinja2.Template(raw_yaml).render(**context)
    #     body = yaml.safe_load(rendered_yaml)

    #     try:
    #         print(f"starting job {namespace}/{job_name}...")
    #         self.batch_v1.create_namespaced_job(namespace=namespace, body=body)
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to create job {namespace}/{job_name}: {e}")


    # def create_job_volume(self, job_config: dict):
    #     """Create Volume backup job"""
    #     context = {
    #         **job_config,
    #         "PVC_NAME": pvc_name,
    #         "MOUNT_PATH": mount_path,
    #     }
    #     self._create_job(
    #         template_path=template_path,
    #         namespace=namespace,
    #         job_name=job_name,
    #         context=context,
    #     )

    # def create_job_postgresql(self, job_config: dict):
    #     """Create PostgreSQL backup job"""
    #     context = {
    #         **job_config,
    #         "POSTGRESQL_IMAGE": postgresql_image,
    #         "POSTGRESQL_IMAGE_PULL_POLICY": postgresql_image_pull_policy,
    #         "POSTGRESQL_IMAGE_PULL_SECRETS": postgresql_image_pull_secrets,
    #     }
    #     self._create_job(
    #         template_path=job_config["TEMPLATE_PATH"],
    #         namespace=job_config["NAMESPACE"],
    #         job_name=job_config["JOB_NAME"],
    #         context=job_config,
    #     )

    # def create_job_volume(
    #     self,
    #     template_path: str,
    #     namespace: str,
    #     image: str,
    #     image_pull_policy: str,
    #     job_name: str,
    #     storage_protocol: str,
    #     storage_endpoint: str,
    #     storage_root: str,
    #     storage_username: str,
    #     storage_password: str,
    #     archive_name: str,
    #     pvc_name: str,
    #     mount_path: str,
    # ):
    #     context = {
    #         "NAMESPACE": namespace,
    #         "IMAGE": image,
    #         "IMAGE_PULL_POLICY": image_pull_policy,
    #         "JOB_NAME": job_name,
    #         "STORAGE_PROTOCOL": storage_protocol,
    #         "STORAGE_ENDPOINT": storage_endpoint,
    #         "STORAGE_ROOT": storage_root,
    #         "STORAGE_USERNAME": storage_username,
    #         "STORAGE_PASSWORD": storage_password,
    #         "ARCHIVE_NAME": archive_name,
    #         "PVC_NAME": pvc_name,
    #         "MOUNT_PATH": mount_path,
    #     }
    #     self._create_job(template_path, namespace, job_name, context)

    # def create_job_postgresql(
    #     self,
    #     template_path: str,
    #     namespace: str,
    #     image: str,
    #     image_pull_policy: str,
    #     job_name: str,
    #     storage_protocol: str,
    #     storage_endpoint: str,
    #     storage_root: str,
    #     storage_username: str,
    #     storage_password: str,
    #     archive_name: str,
    #     postgresql_image: str,
    #     postgresql_image_pull_policy: str,
    #     postgresql_image_pull_secrets: list,
    # ):
    #     """Create PostgreSQL backup job"""
    #     context = {
    #         "NAMESPACE": namespace,
    #         "IMAGE": image,
    #         "IMAGE_PULL_POLICY": image_pull_policy,
    #         "JOB_NAME": job_name,
    #         "STORAGE_PROTOCOL": storage_protocol,
    #         "STORAGE_ENDPOINT": storage_endpoint,
    #         "STORAGE_ROOT": storage_root,
    #         "STORAGE_USERNAME": storage_username,
    #         "STORAGE_PASSWORD": storage_password,
    #         "ARCHIVE_NAME": archive_name,
    #         "POSTGRESQL_IMAGE": postgresql_image,
    #         "POSTGRESQL_IMAGE_PULL_POLICY": postgresql_image_pull_policy,
    #         "POSTGRESQL_IMAGE_PULL_SECRETS": postgresql_image_pull_secrets,
    #     }
    #     self._create_job(template_path, namespace, job_name, context)


    def delete_job(self, namespace: str, job_name: str):
        """Delete a Kubernetes job"""
        try:
            print(f"deleting job {namespace}/{job_name}...")
            self.batch_v1.delete_namespaced_job(namespace=namespace, name=job_name, propagation_policy="Foreground")
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to delete job {namespace}/{job_name}: {e}")


    def wait_job_completion(self, namespace: str, job_name: str) -> bool:
        """Wait for a job to complete"""
        print(f"waiting completion of job {job_name}...")

        try:
            start_time = time.time()
            while time.time() - start_time < 600:
                job = self.batch_v1.read_namespaced_job(name=job_name, namespace=namespace)

                if job.status.succeeded and job.status.succeeded > 0:
                    print(f"success: completed job {namespace}/{job_name}")
                    return True

                if job.status.failed and job.status.failed > 0:
                    print(f"error: failed job {namespace}/{job_name}")
                    return False

                time.sleep(3)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to wait for completion of job {job_name}: {e}")
            return False


    def get_workload_replicas(self, namespace: str, workload_kind: str, workload_name: str) -> int:
        """Get current workload (= deployment, statefulset) replicas quantity"""
        try:
            if workload_kind.lower() == "deployment":
                w = self.apps_v1.read_namespaced_deployment(name=workload_name, namespace=namespace)

            elif workload_kind.lower() == "statefulset":
                w = self.apps_v1.read_namespaced_stateful_set(name=workload_name, namespace=namespace)

            return w.spec.replicas
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to get replicas quantity of {workload_kind}/{workload_name}: {e}")
            return None


    def scale_workload_replicas(self, namespace: str, workload_kind: str, workload_name: str, replicas: int):
        """Scale a workload (= deployment, statefulset) to given replicas quantity"""
        body = {"spec": {"replicas": replicas}}
        try:
            if workload_kind.lower() == "deployment":
                self.apps_v1.patch_namespaced_deployment(name=workload_name, namespace=namespace, body=body)

            elif workload_kind.lower() == "statefulset":
                self.apps_v1.patch_namespaced_stateful_set(name=workload_name, namespace=namespace, body=body)

            print(f"scaled {workload_kind}/{workload_name} to {replicas} replicas")
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to scale {workload_kind}/{workload_name}: {e}")