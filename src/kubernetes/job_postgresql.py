import yaml
from src.kubernetes.base import KubernetesBase
import src.utils.backup_map as bpm


class PostgresBackupJob(KubernetesBase):
    suffix = "pgdump"


    def __init__(self, context: str = None, namespace: str = None):
        super().__init__(context=context, namespace=namespace)


    def get_job_name(self, pvc_name: str, suffix: str = "pgdump") -> str:
        return super().get_job_name(pvc_name, suffix=suffix)


    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs):
        """Backup PostgreSQL database over network with pg_dump using exact workload configuration."""
        workload_type = kwargs.get("workload_type")
        workload_name = kwargs.get("workload_name")
        target_ip = kwargs.get("target_ip")
        master_ns = kwargs.get("master_ns")

        if not workload_type or not workload_name or not target_ip:
            raise ValueError("workload_type, workload_name, and target_ip are required for PostgresBackupJob.")

        postgres_image = self.get_workload_image(workload_type, workload_name)
        dumper_env_list, original_env_from = self._build_strict_postgres_job_env(
            pvc_name=pvc_name,
            target_ip=target_ip,
            workload_type=workload_type,
            workload_name=workload_name,
            master_ns=master_ns
        )

        replacements = {
            "JOB_NAME": self.get_job_name(pvc_name),
            "NAMESPACE": self.namespace,
            "MOUNTED_PATH": mounted_path,
            "PVC_NAME": pvc_name,
            "POSTGRES_IMAGE": postgres_image,
            "IMAGE_PULL_SECRETS_LIST": kwargs.get("image_pull_secrets", []),
            "dumper_env_list": dumper_env_list,
            "ORIGINAL_ENV_FROM": original_env_from,
            "AFFINITY_DICT": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": [node_name]
                                    }
                                ]
                            }
                        ]
                    }
                }
            } if node_name else None
        }

        self._launch_generic_job(
            template_path="templates/postgresql.exporter.job.yaml",
            replacements=replacements
        )


    def _resolve_container_env_secrets(self, raw_env: list) -> list:
        """Decode any secretKeyRef references in container env list into concrete values."""
        resolved_env = []

        for item in raw_env:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            value = item.get("value")
            value_from = item.get("valueFrom") or item.get("value_from")

            if value_from and isinstance(value_from, dict):
                sec_ref = value_from.get("secretKeyRef") or value_from.get("secret_key_ref")
                if sec_ref:
                    sec_name = sec_ref.get("name")
                    sec_key = sec_ref.get("key")
                    try:
                        payload = self.get_secret_payload(sec_name, self.namespace)
                        if sec_key in payload:
                            value = payload[sec_key]
                            resolved_env.append({"name": name, "value": value})
                            continue
                    except Exception as e:
                        print(f"  warn: failed to resolve secretKeyRef for variable '{name}' ({e})")

            if value is not None:
                resolved_env.append({"name": name, "value": value})

        return resolved_env

    def _get_secret_value_from_file_path(self, workload_type: str, workload_name: str, file_path: str) -> str:
        """Inspects workload volume mounts to resolve which Secret mounted file matches the target path and reads it."""
        try:
            if workload_type.lower() == "statefulset":
                res_obj = self.apps_v1.read_namespaced_stateful_set(name=workload_name, namespace=self.namespace)
            else:
                res_obj = self.apps_v1.read_namespaced_deployment(name=workload_name, namespace=self.namespace)

            pod_spec = res_obj.spec.template.spec
            container = pod_spec.containers[0]

            # 1. Look for volume mount matching the file path directory
            matching_vol_name = None
            sub_key = None

            for v_mount in container.volume_mounts or []:
                if file_path.startswith(v_mount.mount_path):
                    matching_vol_name = v_mount.name
                    # Calculate subpath or key filename
                    sub_key = file_path[len(v_mount.mount_path):].lstrip("/")
                    break

            if matching_vol_name:
                for vol in pod_spec.volumes or []:
                    if vol.name == matching_vol_name and vol.secret:
                        sec_name = vol.secret.secret_name
                        payload = self.get_secret_payload(sec_name, self.namespace)

                        # If secret has exact key or single item
                        if sub_key in payload:
                            return payload[sub_key]
                        elif len(payload) == 1:
                            return list(payload.values())[0]

        except Exception as e:
            print(f"  warn: failed to resolve secret content from file path '{file_path}': {e}")

        return file_path


    def _build_strict_postgres_job_env(self, pvc_name: str, target_ip: str, workload_type: str, workload_name: str, master_ns: str = None) -> tuple[list, list]:
        """
        Extracts container environment, resolves K8s Secrets, and strictly maps
        keys configured in backup-map (prioritizing literal overrides) to standard PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
        """
        # Read postgresql configuration dict directly using master_ns
        pg_map_config = {}
        try:
            saved_map = bpm.get_backup_map(self, master_ns=master_ns)
            for ns in saved_map.get("namespaces", []):
                if ns.get("name") == self.namespace:
                    for claim in ns.get("claims", []):
                        if claim.get("name") == pvc_name:
                            pg_map_config = claim.get("postgresql", {})
                            break
        except Exception as e:
            print(f"  warn: failed to read postgresql config from backup-map ({e})")

        # Security check: disallow plain-text 'password' key in backup-map
        if "password" in pg_map_config:
            raise ValueError(f"Security Error: Plain-text 'password' key is forbidden in backup-map for PVC '{pvc_name}'. Use 'password-env' instead.")

        # Read original container spec from target workload
        original_env_raw = []
        original_env_from_raw = []

        if workload_type.lower() == "statefulset":
            res_obj = self.apps_v1.read_namespaced_stateful_set(name=workload_name, namespace=self.namespace)
        else:
            res_obj = self.apps_v1.read_namespaced_deployment(name=workload_name, namespace=self.namespace)

        c_spec = res_obj.spec.template.spec.containers[0]
        if c_spec.env:
            original_env_raw = self.core_v1.api_client.sanitize_for_serialization(c_spec.env)
        if c_spec.env_from:
            original_env_from_raw = self.core_v1.api_client.sanitize_for_serialization(c_spec.env_from)

        resolved_env = self._resolve_container_env_secrets(original_env_raw)
        env_dict = {e["name"]: e["value"] for e in resolved_env if "name" in e and "value" in e}

        # 1. HOST: 'host' (literal) takes priority over 'host-env', fallback to target_ip
        if "host" in pg_map_config and pg_map_config["host"]:
            host_val = str(pg_map_config["host"])
        elif "host-env" in pg_map_config and pg_map_config["host-env"]:
            env_var_name = pg_map_config["host-env"]
            if env_var_name not in env_dict:
                raise ValueError(f"PostgreSQL host-env variable '{env_var_name}' not found in workload environment for PVC '{pvc_name}'.")
            host_val = env_dict[env_var_name]
        else:
            host_val = target_ip

        resolved_env.append({"name": "PGHOST", "value": host_val})

        # 2. PORT: 'port' (literal) takes priority over 'port-env', fallback to 5432
        if "port" in pg_map_config and pg_map_config["port"]:
            port_val = str(pg_map_config["port"])
        elif "port-env" in pg_map_config and pg_map_config["port-env"]:
            env_var_name = pg_map_config["port-env"]
            if env_var_name not in env_dict:
                raise ValueError(f"PostgreSQL port-env variable '{env_var_name}' not found in workload environment for PVC '{pvc_name}'.")
            port_val = str(env_dict[env_var_name])
        else:
            port_val = "5432"

        resolved_env.append({"name": "PGPORT", "value": port_val})

        # 3. USER: 'user' (literal) takes priority over 'user-env'
        if "user" in pg_map_config and pg_map_config["user"]:
            user_val = str(pg_map_config["user"])
        elif "user-env" in pg_map_config and pg_map_config["user-env"]:
            env_var_name = pg_map_config["user-env"]
            if env_var_name not in env_dict:
                raise ValueError(f"PostgreSQL user-env variable '{env_var_name}' not found in workload environment for PVC '{pvc_name}'.")
            user_val = env_dict[env_var_name]
        else:
            raise ValueError(f"PostgreSQL 'user' or 'user-env' configuration missing in backup-map for PVC '{pvc_name}'.")

        resolved_env.append({"name": "PGUSER", "value": user_val})

        # 4. PASSWORD: Only 'password-env' is permitted for security
        pass_env_var = pg_map_config.get("password-env")
        if not pass_env_var:
            raise ValueError(f"PostgreSQL 'password-env' configuration missing in backup-map for PVC '{pvc_name}'.")

        if pass_env_var not in env_dict:
            raise ValueError(f"PostgreSQL password-env variable '{pass_env_var}' not found in workload environment for PVC '{pvc_name}'.")

        pass_val = env_dict[pass_env_var]

        # If pass_val is a file path (starts with /), resolve the actual secret content
        if isinstance(pass_val, str) and pass_val.startswith("/"):
            pass_val = self._get_secret_value_from_file_path(workload_type, workload_name, pass_val)

        resolved_env.append({"name": "PGPASSWORD", "value": pass_val})

        # 5. DATABASE (Optional): 'db' (literal) takes priority over 'db-env'
        if "db" in pg_map_config and pg_map_config["db"]:
            db_val = str(pg_map_config["db"])
            resolved_env.append({"name": "PGDATABASE", "value": db_val})
        elif "db-env" in pg_map_config and pg_map_config["db-env"]:
            env_var_name = pg_map_config["db-env"]
            if env_var_name in env_dict:
                db_val = env_dict[env_var_name]
                resolved_env.append({"name": "PGDATABASE", "value": db_val})

        return resolved_env, original_env_from_raw