"""Api glue for the ``windows-portable`` installer target (Task 25)."""

from __future__ import annotations

from typing import Any

from tokenvault.manager.api_models import ApiError
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.packager import PackagerError
from tokenvault.manager.portable_packager import PortablePackager, default_runtime_dir


def create_portable_installer(m: Manager, team: str, label: str | None) -> dict[str, Any]:
    """Build the no-exe ZIP for ``team`` (caller holds ``m.installer_lock``)."""
    packager = PortablePackager(default_runtime_dir(), m.enrollment, m.cert, m.paths.installers,
                                m.endpoint, m.manager_id)
    try:
        built = packager.build_for_team(team, label)
    except PackagerError as e:
        raise ApiError(str(e)) from e
    return {"path": str(built.path), "file_name": built.file_name, "team": built.team,
            "bytes": built.bytes, "package_id": built.package_id, "target": "windows-portable"}
