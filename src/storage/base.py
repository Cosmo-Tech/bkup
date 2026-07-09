from abc import ABC, abstractmethod

class Storage(ABC):
    """Abstract class defining the contract for all storage providers."""

    @abstractmethod
    def __init__(self, storage_address: str, storage_username: str, storage_password: str):
        pass

    @abstractmethod
    def upload_stream(self, pvc_name: str, path: str, stream) -> bool:
        """Upload a continuous data stream to the target storage."""
        pass