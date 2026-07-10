import io
import os
import tarfile
import threading


class TarGenerator:
    """on-the-fly tar.gz generator to stream data without local disk footprint & small RAM usage."""

    def __init__(self, target_path: str):
        self.target_path = target_path
        self.chunk_size = 65536 # Max data quantity in Ko

    def _write_tar(self, pipe_write):
        """Worker function executed in a separate thread to feed the pipe."""
        # Wrap the pipe's write file descriptor into a file object
        with open(pipe_write, "wb") as fileobj:
            with tarfile.open(fileobj=fileobj, mode="w:gz") as tar:
                # tar.add writes sequentially into the pipe, avoiding massive RAM usage
                tar.add(self.target_path, arcname=os.path.basename(self.target_path))

    def __iter__(self):
        pipe_read, pipe_write = os.pipe()

        # Start the compression process in a dedicated worker thread
        writer_thread = threading.Thread(target=self._write_tar, args=(pipe_write,))
        writer_thread.start()

        # Close the write descriptor in the main thread (only the worker thread should write)
        os.close(pipe_write)

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