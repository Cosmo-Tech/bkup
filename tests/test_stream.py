import io
import gzip
from src.utils.stream import GzipStreamer

def test_gzip_streamer():
    # Génération de données factices
    raw_data = b"Hello Kubernetes Backup! " * 1000
    source_stream = io.BytesIO(raw_data)
    
    # Instanciation de notre streamer avec une petite taille de chunk pour forcer l'itération
    streamer = GzipStreamer(source_stream, chunk_size=512)
    
    # Collecte des chunks compressés
    compressed_chunks = list(streamer)
    compressed_data = b"".join(compressed_chunks)
    
    # Vérifications de base
    assert len(compressed_data) > 0
    assert compressed_data != raw_data
    
    # Test critique : la décompression du flux généré redonne-t-elle la donnée initiale ?
    decompressed_data = gzip.decompress(compressed_data)
    assert decompressed_data == raw_data