timer=120

echo "scale to 1"
kubectl -n keycloak scale --replicas=1 statefulset keycloak-postgresql
kubectl get volumeattachments
python -m src.master -c kob-dev-devops -n keycloak -m


echo "waiting $timer sec before scale to 0"
sleep $timer
kubectl -n keycloak scale --replicas=0 statefulset keycloak-postgresql
kubectl get volumeattachments
python -m src.master -c kob-dev-devops -n keycloak -m


echo "waiting $timer sec before scale to 1"
sleep $timer
kubectl -n keycloak scale --replicas=1 statefulset keycloak-postgresql
kubectl get volumeattachments
python -m src.master -c kob-dev-devops -n keycloak -m
