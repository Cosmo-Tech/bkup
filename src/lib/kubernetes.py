import os
import time
import yaml
import jinja2
import base64
from pathlib import Path
from kubernetes import client, config

import src.lib.utils as utils


class Kubernetes:
    def __init__(self, context: str = None):
        """Init Kubernetes connexion"""
        is_in_cluster = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")

        # # Ensure not running on default context in case of CLI usage
        # if not context and not is_in_cluster:
        #     raise ValueError("missing cluster context")

        self.context = context

        try:
            if is_in_cluster:
                # print("detected in_cluster context\n")
                config.load_incluster_config()
            else:
                # print(f"using kube context {self.context}\n")
                config.load_kube_config(context=self.context)
        except Exception as e:
            raise RuntimeError(f"error: {utils.current_function()}: failed to initialize Kubernetes connection with context '{self.context}': {e}")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        self.auth_v1 = client.AuthorizationV1Api()
        # self.storage_v1 = client.StorageV1Api()


    def list_namespaces(self) -> list:
        """List namespaces"""
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
        """List deployments of a namespace"""
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
        """List statefulsets of a namespace"""
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


    def create_job(self, job_config: dict):
        """Create a Kubernetes job from a template and configuration dictionary."""
        template_path   = job_config["TEMPLATE_PATH"]
        namespace       = job_config["JOB_NAMESPACE"]
        job_name        = job_config["JOB_NAME"]

        raw_yaml = Path(template_path).read_text(encoding="utf-8")
        rendered_yaml = jinja2.Template(raw_yaml).render(**job_config)
        body = yaml.safe_load(rendered_yaml)

        try:
            print(f"starting job {namespace}/{job_name}...")
            self.batch_v1.create_namespaced_job(namespace=namespace, body=body)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to create job {namespace}/{job_name}: {e}")
            raise


    def delete_job(self, namespace: str, job_name: str):
        """Delete a Kubernetes job"""
        try:
            print(f"deleting job {namespace}/{job_name}...")
            self.batch_v1.delete_namespaced_job(namespace=namespace, name=job_name, propagation_policy="Foreground")
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to delete job {namespace}/{job_name}: {e}")


    def get_job_status(self, namespace: str, job_name: str) -> str:
        """Get status of a job"""
        try:
            job = self.batch_v1.read_namespaced_job(namespace=namespace, name=job_name)

            if job.status.succeeded:
                status = 'succeeded'
            elif job.status.failed:
                status = 'failed'
            elif job.status.active:
                status = "active"
            else:
                status = 'pending'

            return status
        except Exception as e:
            print(f"error: {utils.current_function()}: {e}")
            return None


    def wait_job_end(self, namespace: str, job_name: str) -> bool:
        """Wait for a job to end"""
        print(f"waiting end of job {job_name}...")

        try:
            start_time = time.time()
            while time.time() - start_time < 600:
                try:
                    job = self.batch_v1.read_namespaced_job(namespace=namespace, name=job_name)
                    status = self.get_job_status(namespace=namespace, job_name=job_name)

                    if status == 'succeeded':
                        print(f"success: completed job {namespace}/{job_name}")
                        return True

                    if status == 'failed':
                        print(f"error: failed job {namespace}/{job_name}")
                        return False

                except client.exceptions.ApiException as api_error:
                    if api_error.status == 404:
                        print(f"warning: job {namespace}/{job_name} not found (might have been deleted during abort).")
                        return False
                    else:
                        # Re-raise unexpected API errors
                        raise

                time.sleep(3)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to wait for end of job {job_name}: {e}")
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


    # def scale_workload_replicas(self, namespace: str, workload_kind: str, workload_name: str, replicas: int):
    #     """Scale a workload (= deployment, statefulset) to given replicas quantity"""
    #     body = {"spec": {"replicas": replicas}}
    #     try:
    #         if workload_kind.lower() == "deployment":
    #             self.apps_v1.patch_namespaced_deployment(name=workload_name, namespace=namespace, body=body)

    #         elif workload_kind.lower() == "statefulset":
    #             self.apps_v1.patch_namespaced_stateful_set(name=workload_name, namespace=namespace, body=body)

    #         print(f"scaled {workload_kind}/{workload_name} to {replicas} replicas")
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to scale {workload_kind}/{workload_name}: {e}")

    def scale_workload_replicas(self, namespace: str, workload_kind: str, workload_name: str, replicas: int):
        """Scale a workload (= deployment, statefulset) to given replicas quantity"""

        # Ensure replicas is strictly cast to integer to avoid Kubernetes 422 API errors
        replicas = int(replicas)

        body = {"spec": {"replicas": replicas}}
        try:
            if workload_kind.lower() == "deployment":
                self.apps_v1.patch_namespaced_deployment(name=workload_name, namespace=namespace, body=body)

            elif workload_kind.lower() == "statefulset":
                self.apps_v1.patch_namespaced_stateful_set(name=workload_name, namespace=namespace, body=body)

            print(f"scaled {workload_kind}/{workload_name} to {replicas} replicas")
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to scale {workload_kind}/{workload_name}: {e}")

    # def scale_workload_replicas(self, namespace: str, workload_kind: str, workload_name: str, replicas: int):
    #     """Scale a workload (= deployment, statefulset) to given replicas quantity"""
    #     # Ensure replicas is strictly cast to integer to avoid Kubernetes 422 API errors
    #     try:
    #         target_replicas = int(replicas)
    #     except (ValueError, TypeError):
    #         print(f"error: {utils.current_function()}: replicas count must be an integer, got '{replicas}'")
    #         return

    #     body = {"spec": {"replicas": target_replicas}}
    #     try:
    #         if workload_kind.lower() == "deployment":
    #             # self.apps_v1.patch_namespaced_deployment_scale(namespace=namespace, name=workload_name, body=body)
    #             self.apps_v1.patch_namespaced_deployment(namespace=namespace, name=workload_name, body=body)

    #         elif workload_kind.lower() == "statefulset":
    #             # self.apps_v1.patch_namespaced_stateful_set_scale(namespace=namespace, name=workload_name, body=body)
    #             self.apps_v1.patch_namespaced_stateful_set(namespace=namespace, name=workload_name, body=body)

    #         print(f"scaled {workload_kind}/{workload_name} to {target_replicas} replicas")
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to scale {workload_kind}/{workload_name}: {e}")


    def add_workload_annotation(self, namespace: str, workload_kind: str, workload_name: str, annotations: dict):
        """Add or update annotations on a workload (= deployment, statefulset)"""
        body = {"metadata": {"annotations": annotations}}
        try:
            if workload_kind.lower() == "deployment":
                self.apps_v1.patch_namespaced_deployment(name=workload_name, namespace=namespace, body=body)

            elif workload_kind.lower() == "statefulset":
                self.apps_v1.patch_namespaced_stateful_set(name=workload_name, namespace=namespace, body=body)

            print(f"successfully added annotations {annotations} to {workload_kind}/{workload_name}")
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to add annotations to {workload_kind}/{workload_name}: {e}")


    def get_workload_annotation(self, namespace: str, workload_kind: str, workload_name: str, annotation_key: str) -> str:
        """Get value of a given annotation from a workload (= deployment, statefulset)"""
        try:
            if workload_kind.lower() == "deployment":
                w = self.apps_v1.read_namespaced_deployment(name=workload_name, namespace=namespace)

            elif workload_kind.lower() == "statefulset":
                w = self.apps_v1.read_namespaced_stateful_set(name=workload_name, namespace=namespace)

            annotations = w.metadata.annotations
            if annotations:
                return annotations.get(annotation_key)
            
            return None
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to read annotation '{annotation_key}' of {workload_kind}/{workload_name}: {e}")
            return None


    def get_secret_value(self, namespace: str, secret_name: str, key: str) -> str:
        """Get value of a given secret key"""
        try:
            secret = self.core_v1.read_namespaced_secret(namespace=namespace, name=secret_name)
            
            if not secret.data or key not in secret.data:
                print(f"error: key '{key}' not found in secret {namespace}/{secret_name}")
                return None
                
            encoded_val = secret.data[key]
            return base64.b64decode(encoded_val).decode('utf-8')
            
        except client.exceptions.ApiException as api_err:
            if api_err.status == 404:
                print(f"error: secret {namespace}/{secret_name} not found")
            else:
                print(f"error: {utils.current_function()}: Kubernetes API error reading secret {namespace}/{secret_name}: {api_err}")
            return None
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to read secret {namespace}/{secret_name}: {e}")
            return None


    def duplicate_secret(self, source_namespace:str, source_name: str, new_namespace:str, new_name:str):
        """Duplicate a secret to another namespace"""
        try:
            source_secret = self.core_v1.read_namespaced_secret(namespace=source_namespace, name=source_name)

            source_secret.metadata.name = new_name
            source_secret.metadata.namespace = new_namespace
            source_secret.metadata.uid = None
            source_secret.metadata.resource_version = None
            source_secret.metadata.creation_timestamp = None
            source_secret.metadata.managed_fields = None

            new_secret = self.core_v1.create_namespaced_secret(namespace=new_namespace, body=source_secret)
            print(f"success: secret {source_namespace}/{source_name} has been duplicated to {new_namespace}/{new_name}")
            return new_secret

        except client.exceptions.ApiException as api_error:
            if api_error.status == 409:
                print(f"secret {new_namespace}/{new_name} already exists, skipping creation")
            else:
                print(f"error: {utils.current_function()}: Kubernetes API error creating secret {new_namespace}/{new_name}: {api_error}")
                raise

        except Exception as e:
            print(f"error: {utils.current_function()}: failed to duplicate secret {source_namespace}/{source_name} to {new_namespace}/{new_name} {e}")
            raise


    def delete_secret(self, namespace: str, secret_name: str):
        """Delete a Kubernetes secret"""
        try:
            self.core_v1.delete_namespaced_secret(name=secret_name, namespace=namespace)
            print(f"deleted secret {namespace}/{secret_name}")
        except client.exceptions.ApiException as api_error:
            if api_error.status == 404:
                # Already deleted
                pass
            else:
                print(f"error: {utils.current_function()}: failed to delete secret {namespace}/{secret_name}: {api_error}")


    def can_i_create_secrets(self, namespace: str) -> bool:
        """Check if the current service account has permission to create secrets in the target namespace"""
        # Build the access review request
        action = client.V1ResourceAttributes(namespace=namespace, verb="create", resource="secrets")
        access_review = client.V1SelfSubjectAccessReview(spec=client.V1SelfSubjectAccessReviewSpec(resource_attributes=action))
        
        try:
            result = self.auth_v1.create_self_subject_access_review(access_review)
            return result.status.allowed
        except Exception as e:
            print(f"warning: failed to check RBAC permissions in namespace {namespace}: {e}")
            return False


    def get_current_namespace(self):
        """Get namespace of the current working workload"""
        try:
            ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
            if os.path.exists(ns_path):
                with open(ns_path) as f:
                    return f.read().strip()
        except Exception as e:
            print(f"error: failed to get current namespace: {e}")
            return None

    def list_bkup_instances(self) -> list:
        """List all bkup instances in the cluster"""
        try:
            instances = []
            service_accounts = self.core_v1.list_service_account_for_all_namespaces(label_selector="app.kubernetes.io/name=bkup")

            for sa in service_accounts.items:
                instances.append({
                    "name": sa.metadata.name,
                    "namespace": sa.metadata.namespace
                })
            return instances
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list bkup instances: {e}")
            return []

    def get_bkup_instance_envs(self, namespace: str, instance_name: str) -> dict:
        """Fetch environment variables from the bkup instance ConfigMap"""
        try:
            cm = self.core_v1.read_namespaced_config_map(namespace=namespace, name=instance_name)
            envs = cm.data

            # # Automatically set the orchestrator namespace to where the instance lives
            # envs['BKUP_ORCHESTRATOR_NAMESPACE'] = namespace

            return envs
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to read ConfigMap {namespace}/{instance_name}: {e}")
            return {}