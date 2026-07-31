import src.utils.general as general_helper


def get_config_secret_name():
    """Get the 'config' Secret name, which is linked to the installation name."""
    return f"{general_helper.get_installation_name()}-backup-map"


def get_config_secret_namespace(kube) -> str:
    """Find the namespace containing the 'config' Secret."""
    try:
        for ns in kube.list_namespaces():
            secret_name = f"{general_helper.get_installation_name()}-config"
            if kube.get_secret_payload(f"{secret_name}-config", namespace=ns):
                return ns
    except Exception as e:
        print(f"Error: Secret 'config' not found in any namespace. {e}")