from abc import ABC, abstractmethod

class BaseBackupHandler(ABC):
    def __init__(self, name):
        self.name = name

    @abstractmethod
    async def perform_backup(self):
        pass

    @abstractmethod
    def get_latest_backup_info(self):
        pass