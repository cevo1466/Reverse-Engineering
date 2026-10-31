"""Create report directories whose contents Git ignores by default."""

from pathlib import Path


def create_report_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    # A report may contain DOM, cookies, request bodies, or screenshots. This
    # local rule works for any user-chosen directory name inside a Git tree.
    (path / ".gitignore").write_text("*\n", encoding="utf-8")
