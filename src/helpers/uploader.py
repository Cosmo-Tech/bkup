import os
import tarfile
import threading
import queue
import boto3

class S3PipeUploader:
    def __init__(self, bucket, key):
        self.s3 = boto3.client('s3')
        self.bucket = bucket
        self.key = key
        self.queue = queue.Queue(maxsize=10)
        self.closed = False

    def write(self, data):
        # Cette méthode est appelée par tarfile pour pousser les blocs de données
        if data:
            self.queue.put(data)

    def close(self):
        self.closed = True
        self.queue.put(None)

    def start_upload(self):
        # Lance l'upload S3 dans un thread séparé
        def _stream_to_s3():
            def data_generator():
                while True:
                    data = self.queue.get()
                    if data is None:
                        break
                    yield data
            
            # boto3 gère automatiquement l'upload multipart à partir d'un générateur
            self.s3.upload_fileobj(
                Fileobj=type('Stream', (object,), {'read': lambda s, n=None: next(data_generator(), b'')})(),
                Bucket=self.bucket,
                Key=self.key
            )

        t = threading.Thread(target=_stream_to_s3)
        t.start()
        return t

def backup_large_volume(pvc_mount_path, bucket_name, s3_key_name):
    uploader = S3PipeUploader(bucket_name, s3_key_name)
    upload_thread = uploader.start_upload()

    # On crée l'archive tar.gz directement liée à notre uploader S3
    with tarfile.open(fileobj=uploader, mode="w|gz") as tar:
        # On ajoute le dossier du PVC. tarfile va lire et streamer bloc par bloc
        tar.add(pvc_mount_path, arcname=os.path.basename(pvc_mount_path))
    
    uploader.close()
    upload_thread.join()
    print(f"Backup réussi pour {pvc_mount_path} vers s3://{bucket_name}/{s3_key_name}")