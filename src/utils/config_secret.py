import src.utils.general as general_helper


def get_config_secret_name():
    """Get the 'config' Secret name, which is linked to the installation name."""
    return f"{general_helper.get_installation_name()}-backup-map"


def get_config_secret_namespace(kube_client) -> str:
    """Find the namespace containing the 'config' Secret."""

    secret_name = f"{general_helper.get_installation_name()}-config"

    for ns in kube_client.list_namespaces():
        try:
            if kube_client.get_secret_payload(name=secret_name, namespace=ns):
                return ns
        except Exception:
            continue

    raise RuntimeError("Error: Secret 'config' not found in any namespace.")
