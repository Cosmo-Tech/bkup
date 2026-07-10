class TarGenerator:
    """on-the-fly tar.gz generator to stream data without local disk footprint."""
    def __init__(self, target_path: str):
        self.target_path = target_path
        self.buffer = io.BytesIO()

    def __iter__(self):
        # Open tarfile linked to our memory buffer
        with tarfile.open(fileobj=self.buffer, mode="w:gz") as tar:
            # Add the directory to the archive structure
            tar.add(self.target_path, arcname=os.path.basename(self.target_path))
            
            # While tarfile is compressing, extract data chunk by chunk
            self.buffer.seek(0)
            chunk = self.buffer.read()
            if chunk:
                yield chunk
            self.buffer.seek(0)
            self.buffer.truncate(0)

        # Grab any remaining bytes after tar closure
        self.buffer.seek(0)
        remaining = self.buffer.read()
        if remaining:
            yield remaining