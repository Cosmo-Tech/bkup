import os
import yaml
import time
from kubernetes import client, config


class KubernetesBase:
    def __init__(self, context: str = None, namespace: str = None):
        """Init Kubernetes connexion, detect if CLI or in-cluster"""
        self.context = context
        if context:
            config.load_kube_config(context=context)
        else:
            try:
                config.load_incluster_config()
            except Exception as e:
                print(f"error: {e}")

        self.namespace = namespace if namespace else "default"
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        self.storage_v1 = client.StorageV1Api()


    def get_cluster_name(self) -> str:
        """Dynamically resolve the targeted cluster name context descriptor."""
        if self.context:
            return self.context
        return os.getenv("CLUSTER_NAME", "in-cluster")


    def get_namespace(self) -> str:
        """Return the current context target working namespace."""
        return self.namespace


    def get_current_namespace_from_pod(self) -> str:
        """Get the namespace where pod is running (return default if not found)."""
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            try:
                with open(ns_path, "r") as f:
                    return f.read().strip()
            except IOError:
                pass
                
        return None


    def list_namespaces(self) -> list:
        try:
            ns_list = self.core_v1.list_namespace()
            return [ns.metadata.name for ns in ns_list.items]
        except Exception as e:
            print(f"error: failed to list namespaces: {e}")
            return []


    def list_pvc(self) -> list:
        try:
            pvcs = self.core_v1.list_namespaced_persistent_volume_claim(namespace=self.namespace)
            return [{"name": p.metadata.name} for p in pvcs.items]
        except Exception as e:
            print(f"error: failed to list PVCs in {self.namespace}: {e}")
            return []


    def get_pvc(self, pvc_name: str) -> list:
        """Look for PVC from Deployments and StatefulSets using template specs."""
        targets = []
        try:
            # Deployments
            deployments = self.apps_v1.list_namespaced_deployment(namespace=self.namespace)
            for d in deployments.items:
                mounts = self._list_pvc_mount_points(pvc_name, d.spec.template.spec)
                for m in mounts:
                    m["resource"] = f"Deployment/{d.metadata.name}"
                    targets.append(m)

            # StatefulSets
            statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace=self.namespace)
            for s in statefulsets.items:
                mounts = self._list_pvc_mount_points(pvc_name, s.spec.template.spec)
                for m in mounts:
                    m["resource"] = f"StatefulSet/{s.metadata.name}"
                    targets.append(m)
        except Exception as e:
            print(f"error: failed to scan workload mounts for PVC '{pvc_name}': {e}")
        return targets


    def get_image_pull_secrets(self, resource_type: str, resource_name: str) -> list:
        """Dynamically extract imagePullSecrets names defined on a specific workload resource."""
        try:
            if resource_type.lower() == "statefulset":
                sts = self.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=self.namespace)
                spec = sts.spec.template.spec
            elif resource_type.lower() == "deployment":
                deploy = self.apps_v1.read_namespaced_deployment(name=resource_name, namespace=self.namespace)
                spec = deploy.spec.template.spec
            else:
                return []

            if spec and spec.image_pull_secrets:
                return [s.name for s in spec.image_pull_secrets]
        except Exception as e:
            print(f"  warn: failed to read imagePullSecrets for {resource_type}/{resource_name}: {e}")
            
        return []


    def _list_pvc_mount_points(self, pvc_name: str, pod_spec) -> list:
        """Get all mounted paths in a PVC"""
        results = []
        pod_volume_name = None

        if not pod_spec or not pod_spec.volumes:
            return results

        for vol in pod_spec.volumes:
            if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
                pod_volume_name = vol.name
                break

        if not pod_volume_name:
            return results

        for container in pod_spec.containers:
            if hasattr(container, 'volume_mounts') and container.volume_mounts:
                for mount in container.volume_mounts:
                    if mount.name == pod_volume_name:
                        results.append({
                            "container_name": container.name,
                            "mount_path": mount.mount_path,
                            "read_only": mount.read_only if mount.read_only is not None else False
                        })
        return results


    def get_node_name(self, pvc_name: str) -> str:
        """Try to get node name where PVC is or has been attached."""
        try:
            pvc = self.core_v1.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=self.namespace)
            pv_name = pvc.spec.volume_name
            if not pv_name:
                return ""

            # 1. Try to get node name from VolumeAttachment (workload must be up)
            attachments = self.storage_v1.list_volume_attachment()
            for attach in attachments.items:
                if attach.spec.source.persistent_volume_name == pv_name:
                    if attach.spec.node_name:
                        return attach.spec.node_name

            # 2. Fallback on pvc annotations
            if pvc.metadata.annotations:
                selected_node = pvc.metadata.annotations.get("volume.kubernetes.io/selected-node")
                if selected_node:
                    return selected_node

            # 3. Fallback on PV affinity (Local volumes, blocs dédiés)
            try:
                pv = self.core_v1.read_persistent_volume(name=pv_name)
                if pv.spec.node_affinity and pv.spec.node_affinity.required:
                    for term in pv.spec.node_affinity.required.node_selector_terms:
                        for expr in term.match_expressions:
                            if expr.key in ["kubernetes.io/hostname", "metadata.name"] and expr.values:
                                return expr.values[0]
            except Exception:
                pass

            # 4. In the end, if node name cannot be retrieve, just return empty str
            return ""
        except Exception as e:
            print(f"  error: failed to resolve node attachment for PVC '{pvc_name}': {e}")
            return ""


    def get_replicas(self, resource_type: str, resource_name: str) -> int:
        try:
            if resource_type.lower() == "statefulset":
                sts = self.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=self.namespace)
                return sts.spec.replicas if sts.spec.replicas is not None else 0
            elif resource_type.lower() == "deployment":
                deploy = self.apps_v1.read_namespaced_deployment(name=resource_name, namespace=self.namespace)
                return deploy.spec.replicas if deploy.spec.replicas is not None else 0
        except Exception:
            return 0
        return 0


    def scale_replicas(self, resource_type: str, resource_name: str, replicas: int):
        body = {"spec": {"replicas": replicas}}
        try:
            if resource_type.lower() == "statefulset":
                self.apps_v1.patch_namespaced_stateful_set(name=resource_name, namespace=self.namespace, body=body)
            elif resource_type.lower() == "deployment":
                self.apps_v1.patch_namespaced_deployment(name=resource_name, namespace=self.namespace, body=body)
            print(f"  info: scaled {resource_type}/{resource_name} to {replicas} replicas.")
        except Exception as e:
            print(f"  error: failed to scale {resource_type}/{resource_name}: {e}")


    def get_workload_image(self, resource_type: str, resource_name: str) -> str:
        try:
            if resource_type.lower() == "statefulset":
                sts = self.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=self.namespace)
                return sts.spec.template.spec.containers[0].image
            elif resource_type.lower() == "deployment":
                deploy = self.apps_v1.read_namespaced_deployment(name=resource_name, namespace=self.namespace)
                return deploy.spec.template.spec.containers[0].image
        except Exception:
            return "postgres:latest"
        return "postgres:latest"


    def get_configmap(self, name: str, namespace: str) -> dict:
        """Fetch a ConfigMap from the cluster and return its unpacked data payload."""
        try:
            configmap = self.core_v1.read_namespaced_config_map(name=name, namespace=namespace)
            if not configmap.data:
                return {}
            
            unpacked_data = {}
            for k, v in configmap.data.items():
                if isinstance(v, str) and ("\n" in v or ":" in v or v.strip().startswith("-")):
                    try:
                        parsed_content = yaml.safe_load(v)
                        if isinstance(parsed_content, (dict, list)):
                            if len(configmap.data) == 1:
                                return parsed_content
                            unpacked_data[k] = parsed_content
                        else:
                            unpacked_data[k] = v
                    except yaml.YAMLError:
                        unpacked_data[k] = v
                else:
                    unpacked_data[k] = v
            return unpacked_data
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {}
            print(f"  error: failed to read ConfigMap '{name}': {e}")
            raise e


    def save_to_configmap(self, name: str, namespace: str, data: dict):
        """Create or update a ConfigMap with the provided backup-map data structure."""
        formatted_data = {}
        for k, v in data.items():
            if isinstance(v, str):
                formatted_data[k] = v
            elif isinstance(v, (dict, list)):
                formatted_data[k] = yaml.dump(v, default_flow_style=False)
            else:
                formatted_data[k] = str(v)

        body = client.V1ConfigMap(
            api_version="v1",
            kind="ConfigMap",
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            data=formatted_data
        )
        try:
            self.core_v1.read_namespaced_config_map(name=name, namespace=namespace)
            self.core_v1.patch_namespaced_config_map(name=name, namespace=namespace, body=body)
            print(f"\ninfo: configuration backup-map successfully updated inside configmap {name}")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_config_map(namespace=namespace, body=body)
                print(f"info: configuration backup-map successfully created inside configmap {name}")
            else:
                print(f"error: failed to save ConfigMap {name}: {e}")
                raise e


    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs) -> dict:
        """Create a base job that should be overridden from child classes."""
        pull_secrets = kwargs.get("image_pull_secrets", [])
        if pull_secrets:
            secrets_block = "imagePullSecrets:\n" + "\n".join([f"        - name: {s}" for s in pull_secrets])
        else:
            secrets_block = ""

        return {
            "JOB_NAME": self.get_job_name(pvc_name),
            "NAMESPACE": self.namespace,
            "NODE_NAME": node_name,
            "MOUNTED_PATH": mounted_path,
            "PVC_NAME": pvc_name,
            "IMAGE_PULL_SECRETS_BLOCK": secrets_block
        }


    def _launch_generic_job(self, template_path: str, replacements: dict):
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Template path '{template_path}' not found.")

        with open(template_path, "r") as f:
            template_content = f.read()

        rendered = template_content
        for key, value in replacements.items():
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))

        job_manifest = yaml.safe_load(rendered)
        job_name = job_manifest["metadata"]["name"]

        try:
            self.batch_v1.create_namespaced_job(namespace=self.namespace, body=job_manifest)
            print(f"  info: launched backup job '{job_name}' successfully.")
        except Exception as e:
            print(f"  error: failed to submit job manifest '{job_name}': {e}")
            raise e

            
    def get_job_name(self, pvc_name: str) -> str:
        return f"clustersnap-backup-{pvc_name}"


    def delete_backup_job(self, pvc_name: str):
        job_name = self.get_job_name(pvc_name)
        try:
            self.batch_v1.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Background")
            )
            print(f"  info: deleted backup job {job_name}")
        except client.exceptions.ApiException as e:
            if e.status != 404:
                print(f"  warn: failed to delete job {job_name}: {e}")


    def wait_for_scale_down(self, resource_type: str, resource_name: str, timeout: int = 300, check_interval: int = 5) -> bool:
        """Block execution until the targeted workload replicas drop strictly to 0 according to the API."""
        print(f"  info: waiting for {resource_type}/{resource_name} to scale down to 0...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_replicas = self.get_replicas(resource_type, resource_name)
            
            # Si le control plane valide que les réplicas actifs sont à 0 (ou None/0 si déjà éteint)
            if current_replicas == 0 or current_replicas is None:
                print(f"  info: {resource_type}/{resource_name} successfully stopped.")
                return True
                
            time.sleep(check_interval)
            
        print(f"  warn: timeout reached waiting for {resource_type}/{resource_name} scale down.")
        return False


    def wait_for_volume_detachment(self, pvc_name: str, timeout: int = 300, check_interval: int = 5) -> bool:
        """Block execution until the volume is completely detached from any cluster node."""
        print(f"  info: ensuring PVC '{pvc_name}' volume attachment is fully released...")
        try:
            pvc = self.core_v1.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=self.namespace)
            pv_name = pvc.spec.volume_name
            if not pv_name:
                return True
                
            start_time = time.time()
            while time.time() - start_time < timeout:
                detached = True
                attachments = self.storage_v1.list_volume_attachment()
                for attach in attachments.items:
                    if attach.spec.source.persistent_volume_name == pv_name:
                        # If a volume attachment object exists and is not metadata-only, it's still attached
                        detached = False
                        break
                if detached:
                    print(f"  info: PVC '{pvc_name}' volume is safely detached.")
                    return True
                time.sleep(check_interval)
        except Exception as e:
            print(f"  warn: failed checking attachment status for '{pvc_name}': {e}")
        return False


    def wait_for_job_completion(self, job_name: str, timeout: int = 600, check_interval: int = 5) -> bool:
        """Block execution until the backup Kubernetes Job reaches 'Succeeded' or 'Failed' status."""
        print(f"  info: monitoring job '{job_name}' until completion tracking...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                job = self.batch_v1.read_namespaced_job(name=job_name, namespace=self.namespace)
                if job.status.succeeded and job.status.succeeded > 0:
                    print(f"  info: job '{job_name}' completed successfully.")
                    return True
                if job.status.failed and job.status.failed > 0:
                    print(f"  error: job '{job_name}' failed execution execution block.")
                    return False
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    print(f"  error: job '{job_name}' disappeared during execution.")
                    return False
                print(f"  warn: API error tracking job '{job_name}': {e}")
            time.sleep(check_interval)
        print(f"  error: timeout reached waiting for job '{job_name}' completion.")
        return False


    def wait_for_jobs_deletion(self, pvc_list: list, timeout: int = 60, check_interval: int = 2) -> bool:
        """Block execution during Phase 0 to ensure legacy old cleanup runs are fully purged."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            pending_deletion = False
            for pvc in pvc_list:
                pvc_name = pvc if isinstance(pvc, str) else pvc.get("name")
                job_name = self.get_job_name(pvc_name)
                try:
                    self.batch_v1.read_namespaced_job(name=job_name, namespace=self.namespace)
                    pending_deletion = True  # Job still exists
                except client.exceptions.ApiException as e:
                    if e.status != 404:
                        pending_deletion = True
            if not pending_deletion:
                return True
            time.sleep(check_interval)
        return False