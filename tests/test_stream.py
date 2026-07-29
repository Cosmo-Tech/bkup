import io
import gzip
from src.utils.stream import GzipStreamer


def test_gzip_streamer():
    # Fake data generation
    raw_data = b"Hello Kubernetes Backup! " * 1000
    source_stream = io.BytesIO(raw_data)

    # Instantiate our streamer with a small chunk size to force iteration
    streamer = GzipStreamer(source_stream, chunk_size=512)

    # Collect compressed chunks
    compressed_chunks = list(streamer)
    compressed_data = b"".join(compressed_chunks)

    # Basic verifications
    assert len(compressed_data) > 0
    assert compressed_data != raw_data

    # Critical test: does decompressing the generated stream yield the initial data?
    decompressed_data = gzip.decompress(compressed_data)
    assert decompressed_data == raw_data
