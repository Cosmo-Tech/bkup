from src.utils.psql import is_postgresql_workload


def test_is_postgresql_workload():
    # Positive cases
    assert is_postgresql_workload({"container": "postgres", "resource": "StatefulSet/db"}) is True
    assert is_postgresql_workload({"container": "app", "resource": "Deployment/my-psql-db"}) is True

    # Negative cases
    assert is_postgresql_workload({"container": "nginx", "resource": "Deployment/frontend"}) is False
    assert is_postgresql_workload(None) is False
    assert is_postgresql_workload({}) is False
