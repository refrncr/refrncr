"""Dataset resource resolution for access management."""

from refrncr.ports import UnitOfWorkFactory


class DatasetResourceResolver:
    """Persistence-neutral Dataset existence resolver for resource Grants."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    def __call__(self, dataset_id: str) -> bool:
        with self.uow() as uow:
            return uow.datasets.exists(dataset_id)
