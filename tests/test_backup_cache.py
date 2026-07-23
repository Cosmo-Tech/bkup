from src.utils.backup_cache import find_cached_replicas, update_cached_replicas, remove_cached_resource


def test_find_cached_replicas():
    cache = {
        "namespaces": [
            {"name": "keycloak", "resources": [{"type": "StatefulSet", "name": "keycloak-postgresql", "replicas": 3}]}
        ]
    }

    # Must find the existing resource
    assert find_cached_replicas(cache, "keycloak", "StatefulSet", "keycloak-postgresql") == 3

    # Must not find a non-existent resource
    assert find_cached_replicas(cache, "keycloak", "Deployment", "not-found") is None

    # Must not find in a non-existent namespace
    assert find_cached_replicas(cache, "unknown-ns", "StatefulSet", "keycloak-postgresql") is None


def test_update_cached_replicas():
    cache = {}

    # Add a new entry
    update_cached_replicas(cache, "test-ns", "Deployment", "app", 2)
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app") == 2

    # Update an existing entry
    update_cached_replicas(cache, "test-ns", "Deployment", "app", 5)
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app") == 5


def test_remove_cached_resource():
    cache = {
        "namespaces": [
            {
                "name": "test-ns",
                "resources": [
                    {"type": "Deployment", "name": "app1", "replicas": 1},
                    {"type": "Deployment", "name": "app2", "replicas": 2},
                ],
            }
        ]
    }

    remove_cached_resource(cache, "test-ns", "Deployment", "app1")

    assert find_cached_replicas(cache, "test-ns", "Deployment", "app1") is None
    assert find_cached_replicas(cache, "test-ns", "Deployment", "app2") == 2
