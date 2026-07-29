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
        dumper_env_list, original_env_from = self._build_strict_postgres_job_env(pvc_name=pvc_name, target_ip=target_ip, master_ns=master_ns)

        replacements = {
            "JOB_NAME": self.get_job_name(pvc_name),
            "NAMESPACE": self.namespace,
            "MOUNTED_PATH": mounted_path,
            "PVC_NAME": pvc_name,
            "POSTGRES_IMAGE": postgres_image,
            "master_ns": master_ns,
            "IMAGE_PULL_SECRETS_LIST": kwargs.get("image_pull_secrets", []),
            "dumper_env_list": dumper_env_list,
            "ORIGINAL_ENV_FROM": original_env_from,
            "AFFINITY_DICT": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": [node_name]}]}]
                    }
                }
            }
            if node_name
            else None,
        }

        self._launch_generic_job(template_path="templates/postgresql.exporter.job.yaml", replacements=replacements)

    def _resolve_secret_ref(self, secret_cfg: dict, default_ns: str) -> str:
        """Helper to safely fetch a secret key payload from Kubernetes API."""
        if not isinstance(secret_cfg, dict):
            return None

        sec_name = secret_cfg.get("name")
        sec_key = secret_cfg.get("key")
        sec_ns = secret_cfg.get("namespace", default_ns)

        if not sec_name or not sec_key:
            return None

        payload = self.get_secret_payload(sec_name, sec_ns)
        if sec_key not in payload:
            raise ValueError(f"Key '{sec_key}' not found in Secret '{sec_name}' (namespace: '{sec_ns}').")

        return payload[sec_key]

    def _build_strict_postgres_job_env(self, pvc_name: str, target_ip: str, master_ns: str = None) -> tuple[list, list]:
        """
        Builds standard PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE exclusively from
        literals or Kubernetes Secrets defined in backup-map.
        """
        pg_map_config = {}
        try:
            saved_map = bpm.get_backup_map(self, master_ns=master_ns)

            # Unpack ConfigMap wrapper if present
            if isinstance(saved_map, dict) and "bkup-backup-map.yaml" in saved_map:
                content = saved_map["bkup-backup-map.yaml"]
                if isinstance(content, str):
                    saved_map = yaml.safe_load(content) or {}
                elif isinstance(content, dict):
                    saved_map = content

            for ns in saved_map.get("namespaces", []):
                if ns.get("name") == self.namespace:
                    for claim in ns.get("claims", []):
                        if claim.get("name") == pvc_name:
                            pg_map_config = claim.get("postgresql", {})
                            break
        except Exception as e:
            print(f"  warn: failed to read postgresql config from backup-map ({e})")

        if "password" in pg_map_config:
            raise ValueError(f"Security Error: Plain-text 'password' key is forbidden in backup-map for PVC '{pvc_name}'. Use 'password-secret'.")

        dumper_env_list = []

        # 1. HOST
        host_val = str(pg_map_config.get("host")) if pg_map_config.get("host") else target_ip
        dumper_env_list.append({"name": "PGHOST", "value": host_val})

        # 2. PORT
        port_val = str(pg_map_config.get("port")) if pg_map_config.get("port") else "5432"
        dumper_env_list.append({"name": "PGPORT", "value": port_val})

        # 3. USER: 'user' (literal) > 'user-secret' > Fallback "postgres"
        user_val = None
        if pg_map_config.get("user"):
            user_val = str(pg_map_config["user"])
        elif "user-secret" in pg_map_config:
            user_val = self._resolve_secret_ref(pg_map_config["user-secret"], self.namespace)

        if not user_val:
            user_val = "postgres"

        dumper_env_list.append({"name": "PGUSER", "value": user_val})

        # 4. PASSWORD: 'password-secret' (REQUIRED)
        pass_val = None
        if "password-secret" in pg_map_config:
            pass_val = self._resolve_secret_ref(pg_map_config["password-secret"], self.namespace)

        if not pass_val:
            raise ValueError(
                f"PostgreSQL 'password-secret' configuration missing or invalid in backup-map for PVC '{pvc_name}'. Format required: password-secret: {{ name: 'secret-name', key: 'password-key' }}"
            )

        dumper_env_list.append({"name": "PGPASSWORD", "value": pass_val})

        # 5. DATABASE (Optional): 'db' (literal) > 'db-secret'
        db_val = None
        if pg_map_config.get("db"):
            db_val = str(pg_map_config["db"])
        elif "db-secret" in pg_map_config:
            db_val = self._resolve_secret_ref(pg_map_config["db-secret"], self.namespace)

        if db_val:
            dumper_env_list.append({"name": "PGDATABASE", "value": db_val})

        return dumper_env_list, []
