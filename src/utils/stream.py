import os
import stat
import gzip
import io
import tarfile
import threading


def is_unix_pipe(path: str) -> bool:
    """Check if the path is a UNIX pipe (FIFO)."""
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except Exception:
        return False


class GzipStreamer:
    """
    Lit un flux binaire brut (comme un pipe pg_dump)
    et génère à la volée un flux compressé en gzip par morceaux (chunks),
    évitant ainsi de charger le dump entier en mémoire RAM.
    """
    def __init__(self, source_stream, chunk_size=65536):
        self.source_stream = source_stream
        self.chunk_size = chunk_size

    def __iter__(self):
        # Utilisation d'un buffer mémoire pour capturer la sortie compressée
        buffer = io.BytesIO()
        # On instancie le compresseur gzip natif sur notre buffer mémoire
        with gzip.GzipFile(fileobj=buffer, mode="wb") as compressor:
            while True:
                chunk = self.source_stream.read(self.chunk_size)
                if not chunk:
                    break
                compressor.write(chunk)
                # On libère la mémoire compressée accumulée dans le buffer
                buffer.seek(0)
                yield buffer.read()
                buffer.seek(0)
                buffer.truncate()
        
        # On vide les derniers octets restants du compresseur (le footer gzip de sécurité)
        buffer.seek(0)
        yield buffer.read()


class TarGenerator:
    """on-the-fly tar.gz generator to stream data without local disk footprint & small RAM usage."""

    def __init__(self, target_path: str):
        self.target_path = target_path
        self.chunk_size = 65536  # Max data quantity in Ko

    def _write_tar(self, fileobj):
        """Worker function executed in a separate thread to feed the pipe."""
        # Wrap the pipe's write file descriptor into a file object
        with fileobj:
            with tarfile.open(fileobj=fileobj, mode="w:gz") as tar:
                # tar.add writes sequentially into the pipe, avoiding massive RAM usage
                tar.add(self.target_path, arcname=os.path.basename(self.target_path))

    def __iter__(self):
        pipe_read, pipe_write = os.pipe()

        # Wrap the pipe's write file descriptor into a file object safely before starting the thread
        fileobj = open(pipe_write, "wb")

        # Start the compression process in a dedicated worker thread
        writer_thread = threading.Thread(target=self._write_tar, args=(fileobj,))
        writer_thread.start()

        try:
            # Read from the pipe sequentially
            with open(pipe_read, "rb") as stream:
                while True:
                    chunk = stream.read(self.chunk_size)
                    if not chunk:
                        break
                    yield chunk
        finally:
            writer_thread.join()