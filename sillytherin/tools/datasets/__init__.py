"""The dataset tools: search the catalogs, download, list, describe and delete."""

from sillytherin.tools.datasets import (
    dataset_delete,
    dataset_describe,
    dataset_download,
    dataset_list,
    dataset_search,
)

__all__ = [
    "dataset_delete",
    "dataset_describe",
    "dataset_download",
    "dataset_list",
    "dataset_search",
]
