import os
from dataclasses import dataclass, field


class SourceResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSource:
    local_path: str
    source_type: str
    original_name: str
    temporary: bool
    cleanup_paths: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: dict = field(default_factory=dict)
    lifecycle_policy: str = "local_managed"


class LocalMountSource:
    def __init__(self, base_mount_path: str):
        self.base_mount_path = base_mount_path

    def resolve(
        self,
        raw_path: str | None,
        field_name: str,
        lifecycle_policy: str = "local_managed",
    ) -> ResolvedSource | None:
        if not raw_path:
            return None

        clean_path = raw_path.strip()
        if not clean_path:
            return None

        candidate = os.path.normpath(clean_path)
        if os.path.isabs(candidate) or candidate.startswith("..") or "/.." in candidate:
            raise SourceResolutionError(
                f"Invalid relative path in {field_name}: {raw_path}"
            )

        mount_root = os.path.abspath(self.base_mount_path)
        full_path = os.path.abspath(os.path.join(mount_root, candidate))
        if os.path.commonpath([mount_root, full_path]) != mount_root:
            raise SourceResolutionError(
                f"Path escapes mount root in {field_name}: {raw_path}"
            )

        return ResolvedSource(
            local_path=full_path,
            source_type="local",
            original_name=os.path.basename(candidate),
            temporary=False,
            cleanup_paths=(),
            diagnostics={"field_name": field_name, "raw_path": raw_path},
            lifecycle_policy=lifecycle_policy,
        )


class SourceResolver:
    def __init__(self, base_mount_path: str):
        self.local_source = LocalMountSource(base_mount_path)

    def resolve_primary(self, raw_path: str | None) -> ResolvedSource | None:
        return self.local_source.resolve(
            raw_path,
            "956$u",
            lifecycle_policy="local_managed",
        )

    def resolve_cover(self, raw_path: str | None) -> ResolvedSource | None:
        return self.local_source.resolve(
            raw_path,
            "956$p",
            lifecycle_policy="local_unmanaged",
        )

    def resolve_additional(self, raw_path: str | None) -> ResolvedSource | None:
        return self.local_source.resolve(
            raw_path,
            "956$q",
            lifecycle_policy="local_unmanaged",
        )

    def resolve_local_path(self, raw_path: str | None, field_name: str) -> str | None:
        resolved = self.local_source.resolve(raw_path, field_name)
        return resolved.local_path if resolved else None
