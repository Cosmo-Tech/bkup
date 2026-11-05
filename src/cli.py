import os
import sys
import argparse
import fsspec

from src.lib.kubernetes import Kubernetes
import src.lib.utils as utils

def get_storage_filesystem():
    """init fsspec filesystem"""
    protocol        = os.getenv('BKUP_STORAGE_PROTOCOL')
    endpoint        = os.getenv('BKUP_STORAGE_ENDPOINT')
    root            = os.getenv('BKUP_STORAGE_ROOT')
    username        = os.getenv('BKUP_STORAGE_USERNAME')
    password        = os.getenv('BKUP_STORAGE_PASSWORD')

    job_namespace   = os.getenv('BKUP_JOB_NAMESPACE')

    if not protocol or not root:
        print("error: missing storage environment variables (BKUP_STORAGE_PROTOCOL, BKUP_STORAGE_ROOT)")
        sys.exit(1)


    # Map our generic username/password to each drivers specific keys
    storage_options = {
            # Azure Blob (abfs)
            "account_name": username,
            "account_key": password,

            # AWS S3 (s3fs)
            "key": username,
            "secret": password,

            # Others
            "username": username,
            "password": password,
        }

    # Overwrite default fsspec endpoint (= s3.amazonaws.com) if it has been defined by user
    if endpoint:
        storage_options["client_kwargs"] = {"endpoint_url": endpoint}

    try:
        fs = fsspec.filesystem(protocol, **storage_options)

        address_prefix = f"{protocol}://{root}/{job_namespace}"
        return fs, address_prefix
    except Exception as e:
        print(f"error: {utils.current_function()}: failed to init remote storage: {e}")
        sys.exit(1)


def cmd_list_backups(args):
    try:
        fs, base_path = get_storage_filesystem()
        
        try:
            files = fs.ls(base_path)
        except FileNotFoundError:
            print(f"no backups found (path {base_path} does not exist yet).")
            return

        print(f"remote storage: {base_path}")

        backups = [f for f in files if fs.info(f).get('type') == 'file']
        
        if not backups:
            print("  no backups found.")
        else:
            for f in backups:
                name = os.path.basename(f)
                info = fs.info(f)
                size_mb = info.get('size', 0) / (1024 * 1024)
                print(f"{size_mb:>8.2f} MB..........{name:<45} ")
    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def cmd_restore(args):
    archive_name = args.archive_name
    print("restore request:")
    print(f"  archive_name..........{archive_name}")

    print("TODO")


def cmd_list_cronjobs(args):
    try:
        cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
        kube = Kubernetes(context=cluster_context)
        
        cronjobs = kube.batch_v1.list_cron_job_for_all_namespaces().items
        
        bkup_cronjobs = [cj for cj in cronjobs if cj.metadata.name.startswith("bkup-")]
        
        if not bkup_cronjobs:
            print("  no backup cronjobs found in cluster.")
            return

        for cj in bkup_cronjobs:
            namespace   = cj.metadata.namespace
            name        = cj.metadata.name
            schedule    = cj.spec.schedule
            suspend     = " (suspended)" if cj.spec.suspend else ""
            print(f"namespace....{namespace}")
            print(f"cronjob......{name}{suspend}")
            print(f"schedule.....{schedule}\n")

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def cmd_list_jobs(args):
    try:
        cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
        kube = Kubernetes(context=cluster_context)
        
        jobs = kube.batch_v1.list_job_for_all_namespaces().items
        bkup_jobs = [j for j in jobs if j.metadata.name.startswith("bkup-")]
        
        if not bkup_jobs:
            print("no backup job found in cluster.")
            return

        for j in bkup_jobs:
            ns = j.metadata.namespace
            name = j.metadata.name
            
            if j.status.succeeded:
                status = "succeeded"
            elif j.status.failed:
                status = "failed"
            elif j.status.active:
                status = "running"
            else:
                status = "pending"
                
            print(f"namespace....{ns}")
            print(f"job..........{name}")
            print(f"status.......{status}")

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="bkup CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: list-backups
    subparsers.add_parser("list-backups", help="List existing backups in remote storage")

    # Command: restore
    parser_restore = subparsers.add_parser("restore", help="Restore a specific backup from remote storage")
    parser_restore.add_argument("archive_name", help="Name of the archive to restore (e.g. psql_hash_yymmddhhmmss.gz)")

    # Command: list-cronjobs
    subparsers.add_parser("list-cronjobs", help="List existing backup cronjobs in Kubernetes")

    # Command: list-jobs
    subparsers.add_parser("list-jobs", help="List recent backup jobs and their status in Kubernetes")

    args = parser.parse_args()

    if args.command == "list-backups":
        cmd_list_backups(args)
    elif args.command == "restore":
        cmd_restore(args)
    elif args.command == "list-cronjobs":
        cmd_list_cronjobs(args)
    elif args.command == "list-jobs":
        cmd_list_jobs(args)

if __name__ == "__main__":
    main()