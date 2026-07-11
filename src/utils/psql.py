import os
import subprocess
import shlex


def is_postgresql_workload(mount_info: dict) -> bool:
    """Heuristic to determine if a mount/container belongs to a PostgreSQL database."""
    if not mount_info:
        return False

    container_name = mount_info.get("container", "").lower()
    resource_name = mount_info.get("resource", "").lower()

    if "postgres" in container_name or "psql" in container_name:
        return True
    if "postgres" in resource_name or "psql" in resource_name:
        return True
        
    return False


def get_superuser_name():
    """Dynamically discover the bootstrap superuser name"""
    # Since we are in 'local trust' mode, we can connect using the local system account
    # and query the system catalog to get the exact admin role name
    command = "psql -t -A -c \"SELECT rolname FROM pg_roles WHERE rolsuper = true ORDER BY oid LIMIT 1;\""
    
    process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    
    if process.returncode != 0:
        # Fallback to 'postgres' if the query fails for any reason
        return "postgres"
        
    return stdout.decode().strip()


def get_all_databases(superuser):
    """Retrieve the list of active databases using the discovered superuser"""
    command = f"psql -U {superuser} -t -A -c \"SELECT datname FROM pg_database WHERE datistemplate = false AND datname NOT IN ('postgres', 'images', 'azure_sys')\""
    
    process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    
    if process.returncode != 0:
        raise Exception(f"Failed to list databases: {stderr.decode()}")
        
    return [db.strip() for db in stdout.decode().split('\n') if db.strip()]


def stream_individual_dumps(storage_provider, pvc_name):
    """Orchestrate the dynamic dump process for all databases"""
    try:
        # 1. Discover the superuser name dynamically
        superuser = get_superuser_name()
        print(f"Superuser detected for backup: {superuser}")
        
        # 2. Get the databases using this user
        databases = get_all_databases(superuser)
        print(f"Databases detected for PVC {pvc_name}: {databases}")
        
        for db in databases:
            print(f"Starting pg_dump for database: {db}...")
            
            # 3. Execute pg_dump with the correct dynamic user
            dump_command = f"pg_dump -U {superuser} -d {db} -F c"
            
            process = subprocess.Popen(shlex.split(dump_command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            success = storage_provider.upload_stream(
                pvc_name=f"{pvc_name}-{db}", 
                path=f"/{db}.dump", 
                data_generator=process.stdout 
            )
            
            process.wait()
            if process.returncode != 0:
                print(f"Error while dumping {db}: {process.stderr.read().decode()}")
            else:
                print(f"Backup for {db} successfully streamed to Azure.")

        # 4. Dump globals with the correct user
        print("Streaming postgresql-globals...")
        globals_cmd = f"pg_dumpall -U {superuser} --globals-only"
        process_globals = subprocess.Popen(shlex.split(globals_cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        storage_provider.upload_stream(
            pvc_name=f"{pvc_name}-globals",
            path="/postgresql-globals.sql",
            data_generator=process_globals.stdout
        )
        process_globals.wait()
        print("Globals successfully streamed to Azure.")
                
    except Exception as e:
        print(f"Dump orchestration failed: {e}")