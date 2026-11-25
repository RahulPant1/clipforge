from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def atomic_output(destination: Path) -> Iterator[Path]:
    """Publish a completed file with a same-filesystem rename."""
    with TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as directory:
        staged = Path(directory) / destination.name
        yield staged
        staged.replace(destination)
