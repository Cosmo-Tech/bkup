import os
import yaml
import time
import hashlib
from kubernetes import client, config


class KubernetesBase:
    def __init__(self, context: str = None, namespace: str = None):
        """Init Kubernetes connexion."""
        is_in_cluster = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")

        # Security to force give a context (to avoid using default context in case of CLI usage)
        if not context and not is_in_cluster:
            raise ValueError("No cluster context provided. Please specify a target context using '--context' (-c).")

        self.context = context
        self.namespace = namespace if namespace else "default"

        try:
            if is_in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(context=self.context)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Kubernetes connection with context '{self.context}': {e}")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        self.storage_v1 = client.StorageV1Api()

    def get_cluster_name(self) -> str:
        """Dynamically resolve the targeted cluster name context descriptor."""
        if self.context:
            return self.context
        return "in-cluster"

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

    def get_pod_ip(self, resource_type: str, resource_name: str, pvc_name: str) -> str:
        """Find the exact IP of the running Pod for a specific workload resource."""
        pods = self.core_v1.list_namespaced_pod(namespace=self.namespace)

        for pod in pods.items:
            if pod.status.phase != "Running" or not pod.status.pod_ip:
                continue

            # 1. Primary check: OwnerReferences matches the Deployment / StatefulSet name
            if pod.metadata.owner_references:
                for owner in pod.metadata.owner_references:
                    if owner.name == resource_name or pod.metadata.name.startswith(resource_name):
                        return pod.status.pod_ip

            # 2. Fallback check: Match PVC name inside volumes spec
            if pod.spec.volumes:
                for vol in pod.spec.volumes:
                    if vol.persistent_volume_claim and (vol.persistent_volume_claim.claim_name == pvc_name or pvc_name in vol.persistent_volume_claim.claim_name):
                        return pod.status.pod_ip

        raise RuntimeError(f"Could not find any running Pod attached to {resource_type}/{resource_name} (PVC '{pvc_name}') in namespace '{self.namespace}'")

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
        """Get all mounted paths in a PVC."""
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
            if hasattr(container, "volume_mounts") and container.volume_mounts:
                for mount in container.volume_mounts:
                    if mount.name == pod_volume_name:
                        results.append(
                            {
                                "container_name": container.name,
                                "mount_path": mount.mount_path,
                                "read_only": mount.read_only if mount.read_only is not None else False,
                            }
                        )
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

            # 3. Fallback on PV affinity (Local volumes, dedicated blocks)
            try:
                pv = self.core_v1.read_persistent_volume(name=pv_name)
                if pv.spec.node_affinity and pv.spec.node_affinity.required:
                    for term in pv.spec.node_affinity.required.node_selector_terms:
                        for expr in term.match_expressions:
                            if expr.key in ["kubernetes.io/hostname", "metadata.name"] and expr.values:
                                return expr.values[0]
            except Exception:
                pass

            # 4. If node name cannot be retrieved, return empty string
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
        """Extract exact container image used by workload. Raises ValueError if missing."""
        try:
            if resource_type.lower() == "statefulset":
                sts = self.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=self.namespace)
                containers = sts.spec.template.spec.containers

            elif resource_type.lower() == "deployment":
                deploy = self.apps_v1.read_namespaced_deployment(name=resource_name, namespace=self.namespace)
                containers = deploy.spec.template.spec.containers

            else:
                raise ValueError(f"Unsupported resource type '{resource_type}'")

            if containers and containers[0].image:
                return containers[0].image
        except Exception as e:
            raise ValueError(f"Failed to read image for {resource_type}/{resource_name}: {e}")

        raise ValueError(f"No image defined on container for {resource_type}/{resource_name}")

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
            data=formatted_data,
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

    def get_secret_payload(self, name: str, namespace: str) -> dict:
        """Centralized and safe extraction of Secret base64 values decoded into a plain text map."""
        import base64

        try:
            secret = self.core_v1.read_namespaced_secret(name=name, namespace=namespace)
            if not secret.data:
                return {}
            return {k: base64.b64decode(v).decode("utf-8") for k, v in secret.data.items()}
        except Exception:
            return {}

    def get_job_name(self, pvc_name: str, suffix: str = "") -> str:
        """Generates the unique Job name with the original prefix under the 63 characters limit."""
        prefix = "bkup-backup-"
        suffix_str = f"-{suffix}" if suffix else ""
        candidate_name = f"{prefix}{pvc_name}{suffix_str}"

        if len(candidate_name) <= 63:
            return candidate_name

        # Cut name & set a hash if too long
        pvc_hash = hashlib.md5(pvc_name.encode("utf-8")).hexdigest()[:8]
        allowed_pvc_len = 63 - (len(prefix) + len(pvc_hash) + 1 + len(suffix_str))
        truncated_pvc = pvc_name[:allowed_pvc_len]
        return f"{prefix}{truncated_pvc}-{pvc_hash}{suffix_str}"

    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs) -> dict:
        """Create a base job layout configuration block."""
        return {}

    def _launch_generic_job(self, template_path: str, replacements: dict):
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Template path '{template_path}' not found.")

        master_ns = replacements.get("master_ns") or self.get_current_namespace_from_pod() or "default"
        secret_data = self.get_secret_payload("bkup-config", master_ns)

        with open(template_path, "r") as f:
            template_content = f.read()

        rendered = template_content
        rendered = rendered.replace("{{ JOB_NAME }}", replacements.get("JOB_NAME", ""))
        rendered = rendered.replace("{{ NAMESPACE }}", replacements.get("NAMESPACE", ""))
        rendered = rendered.replace("{{ PVC_NAME }}", replacements.get("PVC_NAME", ""))
        rendered = rendered.replace("{{ MOUNTED_PATH }}", replacements.get("MOUNTED_PATH", ""))

        if "{{ POSTGRES_IMAGE }}" in rendered:
            if "POSTGRES_IMAGE" not in replacements or not replacements["POSTGRES_IMAGE"]:
                raise ValueError("POSTGRES_IMAGE missing from replacements. Exact workload image is required.")
            rendered = rendered.replace("{{ POSTGRES_IMAGE }}", replacements["POSTGRES_IMAGE"])

        job_manifest = yaml.safe_load(rendered)
        pod_spec = job_manifest["spec"]["template"]["spec"]

        if replacements.get("AFFINITY_DICT"):
            pod_spec["affinity"] = replacements["AFFINITY_DICT"]
        else:
            pod_spec.pop("affinity", None)

        if replacements.get("IMAGE_PULL_SECRETS_LIST"):
            pod_spec["imagePullSecrets"] = [{"name": s} for s in replacements["IMAGE_PULL_SECRETS_LIST"]]

        pod_spec["tolerations"] = [
            {"operator": "Exists", "effect": "NoSchedule"},
            {"operator": "Exists", "effect": "NoExecute"},
        ]

        # Inject original envFrom into database-dumper container if present
        if replacements.get("ORIGINAL_ENV_FROM"):
            for container in pod_spec["containers"]:
                if container.get("name") == "database-dumper":
                    container["envFrom"] = replacements["ORIGINAL_ENV_FROM"]

        # Process container environment variables
        for container in pod_spec["containers"]:
            c_name = container.get("name")
            clean_env = []
            final_overrides = {}

            # Read dumper_env_list correctly from replacements dictionary
            if c_name == "database-dumper" and replacements.get("dumper_env_list"):
                raw_env = replacements["dumper_env_list"]
            else:
                raw_env = container.get("env") or []

            for env_item in raw_env:
                if not isinstance(env_item, dict):
                    continue

                name = env_item.get("name")

                if name == "STORAGE_ADDRESS":
                    val = secret_data.get("storage-address")
                    if not val:
                        raise ValueError(f"Required key 'storage-address' missing in Secret 'bkup-config' (namespace: '{master_ns}')")
                    final_overrides[name] = {"name": name, "value": val}

                elif name == "STORAGE_USERNAME":
                    val = secret_data.get("storage-username")
                    if not val:
                        raise ValueError(f"Required key 'storage-username' missing in Secret 'bkup-config' (namespace: '{master_ns}')")
                    final_overrides[name] = {"name": name, "value": val}

                elif name == "STORAGE_PASSWORD":
                    val = secret_data.get("storage-password")
                    if not val:
                        raise ValueError(f"Required key 'storage-password' missing in Secret 'bkup-config' (namespace: '{master_ns}')")
                    final_overrides[name] = {"name": name, "value": val}

                elif name == "CLUSTER_NAME":
                    val = secret_data.get("cluster-name")
                    if not val:
                        raise ValueError(f"Required key 'cluster-name' missing in Secret 'bkup-config' (namespace: '{master_ns}')")
                    final_overrides[name] = {"name": name, "value": val}

                else:
                    clean_env.append(env_item)

            for override_item in final_overrides.values():
                clean_env.append(override_item)

            # Re-assign updated env to container spec
            container["env"] = clean_env

        job_name = job_manifest["metadata"]["name"]

        try:
            self.batch_v1.create_namespaced_job(namespace=self.namespace, body=job_manifest)
            print(f"  info: launched backup job '{job_name}' successfully.")
        except Exception as e:
            print(f"  error: failed to submit job manifest '{job_name}': {e}")
            raise e

    def delete_backup_job(self, pvc_name: str, suffix: str = ""):
        job_name = self.get_job_name(pvc_name, suffix=suffix)
        try:
            self.batch_v1.delete_namespaced_job(name=job_name, namespace=self.namespace, body=client.V1DeleteOptions(propagation_policy="Background"))
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

            if current_replicas == 0 or current_replicas is None:
                print(f"  info: {resource_type}/{resource_name} successfully stopped.")
                return True

            time.sleep(check_interval)

        print(f"  warn: timeout reached waiting for {resource_type}/{resource_name} scale down.")
        return False

    def wait_for_scale_up(self, resource_type: str, resource_name: str, target_replicas: int, timeout: int = 300, check_interval: int = 5) -> bool:
        """Block execution until the specific workload replicas scale back up and its own Pod has an active IP assigned."""
        print(f"  info: waiting for {resource_type}/{resource_name} to scale up to {target_replicas} replica(s)...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            current_replicas = self.get_replicas(resource_type, resource_name)

            if current_replicas >= target_replicas:
                try:
                    pods = self.core_v1.list_namespaced_pod(namespace=self.namespace)
                    for pod in pods.items:
                        if pod.status.phase == "Running" and pod.status.pod_ip:
                            if pod.metadata.owner_references:
                                for owner in pod.metadata.owner_references:
                                    if owner.name == resource_name or pod.metadata.name.startswith(resource_name):
                                        print(f"  info: {resource_type}/{resource_name} pod '{pod.metadata.name}' is Running with IP {pod.status.pod_ip}.")
                                        return True
                except Exception as e:
                    print(f"  warn: error inspecting pods for {resource_type}/{resource_name}: {e}")

            time.sleep(check_interval)

        print(f"  warn: timeout reached waiting for {resource_type}/{resource_name} scale up.")
        return False

    def is_topology_constrained(self, pvc_name: str) -> bool:
        """Analyzes the PV storage driver to detect the presence of localized Cloud storage."""
        try:
            pvc = self.core_v1.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=self.namespace)
            pv_name = pvc.spec.volume_name
            if pv_name:
                pv = self.core_v1.read_persistent_volume(name=pv_name)
                csi_driver = pv.spec.csi.driver.lower() if pv.spec.csi else ""
                if any(cloud in csi_driver for cloud in ["azure", "aws", "ebs", "gce", "gcp"]) or pv.spec.nfs or "file" in csi_driver:
                    return False
                return True
        except Exception as e:
            print(f"      warn: storage topology assessment bypassed ({e}). Defaulting to constrained.")
        return True

    def wait_for_volume_detachment(self, pvc_name: str, timeout: int = 300, check_interval: int = 5) -> bool:
        """Instant check for PVC detachment. Returns immediately if workload was already stopped."""
        print(f"   info: checking PVC '{pvc_name}' status in namespace '{self.namespace}'...")

        pods = self.core_v1.list_namespaced_pod(namespace=self.namespace)
        pvc_is_locked = False
        pods_locking_pvc = []

        for pod in pods.items:
            if pod.metadata.name.startswith("clustersnap-backup-") or pod.metadata.name.startswith("bkup-"):
                continue

            if pod.spec.volumes:
                for vol in pod.spec.volumes:
                    if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
                        if pod.status.phase in ["Running", "Pending"] or pod.metadata.deletion_timestamp is not None:
                            pvc_is_locked = True
                            pods_locking_pvc.append(pod.metadata.name)
                            break

        if not pvc_is_locked:
            print(f"     Volume for PVC '{pvc_name}' is already completely free.")
            return True

        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(check_interval)

        return True

    def wait_for_job_completion(self, job_name: str, timeout: int = 600, check_interval: int = 5) -> bool:
        """Universal CSI-agnostic monitoring for backup Job execution until completion."""
        print(f"  info: monitoring job '{job_name}' until completion tracking...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                job = self.batch_v1.read_namespaced_job(name=job_name, namespace=self.namespace)

                # 1. Job succeeded
                if job.status.succeeded and job.status.succeeded > 0:
                    print(f"  info: job '{job_name}' completed successfully.")
                    return True

                # 2. Job failed
                if job.status.failed and job.status.failed > 0:
                    print(f"  error: job '{job_name}' failed execution block.")
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
                # Must clean up both -volume and -pgdump residual jobs
                for suff in ["volume", "pgdump"]:
                    job_name = self.get_job_name(pvc_name, suffix=suff)
                    try:
                        self.batch_v1.read_namespaced_job(name=job_name, namespace=self.namespace)
                        pending_deletion = True
                    except client.exceptions.ApiException as e:
                        if e.status != 404:
                            pending_deletion = True
            if not pending_deletion:
                return True
            time.sleep(check_interval)
        return False
