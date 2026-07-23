from src.utils.backup_cache import (
    find_cached_replicas,
    update_cached_replicas,
    remove_cached_resource
)

def test_find_cached_replicas():
    cache = {
        "namespaces": [
            {
                "name": "keycloak",
                "resources": [
                    {"type": "StatefulSet", "name": "keycloak-postgresql", "replicas": 3}
                ]
            }
        ]
    }

    # Doit trouver la ressource existante
    assert find_cached_replicas(cache, "keycloak", "StatefulSet", "keycloak-postgresql") == 3

    # Ne doit pas trouver une ressource inexistante
    assert find_cached_replicas(cache, "keycloak", "Deployment", "not-found") is None

    # Ne doit pas trouver dans un namespace inexistant
    assert find_cached_replicas(cache, "unknown-ns", "StatefulSet", "keycloak-postgresql") is None

def test_update_cached_replicas():
    cache = {}

    # Ajout d'une nouvelle entrée
    update_cached_replicas(cache, "test-ns", "Deployment", "app", 2)
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app") == 2

    # Mise à jour d'une entrée existante
    update_cached_replicas(cache, "test-ns", "Deployment", "app", 5)
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app") == 5

def test_remove_cached_resource():
    cache = {
        "namespaces": [
            {
                "name": "test-ns",
                "resources": [
                    {"type": "Deployment", "name": "app1", "replicas": 1},
                    {"type": "Deployment", "name": "app2", "replicas": 2}
                ]
            }
        ]
    }

    remove_cached_resource(cache, "test-ns", "Deployment", "app1")

    assert find_cached_replicas(cache, "test-ns", "Deployment", "app1") is None
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app2") == 2
