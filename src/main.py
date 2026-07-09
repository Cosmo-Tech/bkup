import sys
import argparse
import time
from src.helpers.kubernetes import Kubernetes


# Get a YAML map of what will be backuped
def get_backup_map(kubernetes: Kubernetes):
    current_ns = kubernetes.get_namespace()

    print(f"context: {kubernetes.get_context()}")
    print(f"namespaces:")
    print(f"- name: {current_ns}")

    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("error: no PVC found or unauthorized access.")
        return 0

    # Loop to display the backup map
    print(f"  claims:")
    for pvc in kubernetes.list_pvc():
        print(f"    - name: {pvc['name']}")
        print(f"      status: {pvc['status']}")
        print(f"      capacity: {pvc['capacity']}")

        mounts = kubernetes.get_pvc(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")
        else:
            print(f"      mounts: {mounts}")


# Run a backup job
def run_backup_job(kubernetes: Kubernetes):
    # Display the map before running the job
    get_backup_map(kubernetes)

    # for pvc in kubernetes.list_pvc():
    #     pvc_name = pvc['name']

    #     targets = kubernetes.get_pvc(pvc_name)
    #     if not targets:
    #         print(f"info: no active workload config found for {pvc_name}. Skipping.")
    #         continue

    #     target = targets[0]
    #     mounted_path = target['mount_path']
    #     resource = target['resource']
    #     resource_type, resource_name = resource.split('/')
    #     resource_initial_replicas = kubernetes.get_replicas(resource_type, resource_name)

    #     print(f"replicas quantity found for {resource}: {resource_initial_replicas}")

    #     is_running = resource_initial_replicas > 0
    #     if is_running:
    #         kubernetes.scale_replicas(resource_type, resource_name, replicas=0)
    #         if not kubernetes.wait_for_scale_down(resource_type, resource_name):
    #             print(f"error: scale down fail for {resource}. Aborting backup of {pvc_name}")
    #             continue 
    #     else:
    #         print(f"info: {resource} is already scaled down. Safe to proceed.")

    #     job_success = kubernetes.create_backup_job(pvc=pvc_name, mounted_path=mounted_path)
    #     if is_running:
    #         kubernetes.scale_replicas(resource_type, resource_name, replicas=resource_initial_replicas)
    #         print(f"restored replicas of {resource} {resource_initial_replicas}")

    #     print('')


    """
    Orchestrateur séquentiel global :
    Phase 0: Nettoyage + Attente de suppression des vieux Jobs
    Phase 1: Collecte des infos + Ordre de Scale Down (0)
    Phase 2: Attente de l'extinction complète de la prod
    Phase 3: Lancement de tous les nouveaux Jobs en parallèle via K8s
    Phase 4: Restauration (Scale Up) de la prod à son état initial
    """
    # Récupération de la liste des PVCs du namespace
    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("error: no PVC found to backup.")
        return

    print(f"\n==================================================")
    print(f"⚡ Starting Sequential Backup Orchestration for {len(pvcs)} PVCs")
    print(f"==================================================")

    # ==================================================
    # PHASE 0 : NETTOYAGE GLOBAL DES ANCIENS JOBS
    # ==================================================
    print("\n🧹 PHASE 0: Purging any existing/stale backup jobs...")
    pvc_names = []
    
    for pvc in pvcs:
        pvc_name = pvc['name']
        pvc_names.append(pvc_name)
        # Envoie l'ordre de suppression à l'API (non-bloquant)
        kubernetes.delete_backup_job(pvc_name=pvc_name)
    
    # Validation dynamique de la disparition des anciens Jobs
    if not kubernetes.wait_for_jobs_deletion(pvc_names=pvc_names, timeout=45):
        print("  ⚠️ Warning: Some old jobs might still be terminating. Proceeding anyway...")

    # Structure pour mémoriser l'état initial de chaque workload
    backup_workloads = {}

    # ==================================================
    # PHASE 1 : ANALYSE ET ENVOI DU SCALE DOWN (0)
    # ==================================================
    print("\n🛑 PHASE 1: Capturing states and scaling down workloads...")
    for pvc in pvcs:
        pvc_name = pvc['name']
        targets = kubernetes.get_pvc(pvc_name)
        if not targets:
            print(f"  ⚠️ No active workload config found for {pvc_name}. Skipping.")
            continue

        # On prend la première configuration trouvée liée au PVC
        target = targets[0]
        resource_key = target['resource'] # Ex: "StatefulSet/postgresql"
        resource_type, resource_name = resource_key.split('/')
        mounted_path = target['mount_path']

        # Évite de traiter deux fois le même StatefulSet si celui-ci a plusieurs PVCs
        if resource_key not in backup_workloads:
            # On lit et on stocke le nombre de répliques en cours (ex: 1, 2, 3...)
            initial_replicas = kubernetes.get_replicas(resource_type, resource_name)
            
            backup_workloads[resource_key] = {
                "resource_type": resource_type,
                "resource_name": resource_name,
                "initial_replicas": initial_replicas,
                "pvc_name": pvc_name,
                "mounted_path": mounted_path
            }

            # Si l'application tourne, on demande à K8s de l'éteindre
            if initial_replicas > 0:
                kubernetes.scale_replicas(resource_type, resource_name, replicas=0)
            else:
                print(f"  🌙 {resource_key} is already stopped (Night mode).")

    # ==================================================
    # PHASE 2 : ATTENTE DE L'EXTINCTION GLOBALE
    # ==================================================
    print("\n⏳ PHASE 2: Waiting for all workloads to be fully stopped...")
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            # Bloque le script tant que les conteneurs de l'application ne sont pas à 0
            kubernetes.wait_for_scale_down(info["resource_type"], info["resource_name"])

    # ==================================================
    # PHASE 3 : LANCEMENT DE TOUS LES BACKUPS D'UN COUP
    # ==================================================
    print("\n🚀 PHASE 3: Triggering all Kubernetes backup jobs simultaneously...")
    for resource_key, info in backup_workloads.items():
        # K8s instancie les Jobs en tâche de fond (le traitement disque se fait en parallèle sur le cluster)
        kubernetes.create_backup_job(pvc_name=info["pvc_name"], mounted_path=info["mounted_path"])

    # Temporisation de sécurité pour laisser le temps aux Pods de backup de démarrer,
    # d'accrocher le volume et de poser leurs verrous de lecture.
    print("  🛌 Allowing a 5s safety buffer for backup containers to initialize...")
    time.sleep(5)

    # ==================================================
    # PHASE 4 : RESTAURATION (SCALE UP) DE LA PRODUCTION
    # ==================================================
    print("\n🔄 PHASE 4: Restoring production workloads to initial states...")
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            # On remet l'application exactement dans son état de départ (ex: replicas=1)
            kubernetes.scale_replicas(
                info["resource_type"], 
                info["resource_name"], 
                replicas=info["initial_replicas"]
            )
            print(f"  ✅ {resource_key} restored to {info['initial_replicas']} replicas.")

    print(f"\n==================================================")
    print(f"🏁 Sequential orchestration process finished successfully.")
    print(f"==================================================")


def main():
    parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
    parser.add_index = False
    parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
    parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace")
    parser.add_argument("-j", "--job", action="store_true", help="Run a backup job")
    parser.add_argument("-m", "--map", action="store_true", help="Get the backup map")
    args = parser.parse_args()

    kubernetes = Kubernetes(context=args.context, namespace=args.namespace)
    
    if args.job:
        run_backup_job(kubernetes)

    elif args.map:
        get_backup_map(kubernetes)

    else:
        print('please run with --job or --map (--help for more info)')


if __name__ == "__main__":
    sys.exit(main())
