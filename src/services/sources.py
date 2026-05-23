import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse


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


@dataclass(frozen=True)
class GoogleDriveFileRef:
    file_id: str
    resource_key: str | None = None
    original_url: str = ""


class GoogleDriveUrlParser:
    SUPPORTED_HOSTS = {"drive.google.com", "www.drive.google.com"}

    def parse(self, raw_url: str | None, field_name: str) -> GoogleDriveFileRef | None:
        if not raw_url:
            return None

        clean_url = raw_url.strip()
        parsed = urlparse(clean_url)

        if parsed.scheme not in ("http", "https"):
            return None

        if parsed.netloc.lower() not in self.SUPPORTED_HOSTS:
            raise SourceResolutionError(
                f"Unsupported URL in {field_name}: only Google Drive file URLs are allowed"
            )

        path_parts = [part for part in parsed.path.split("/") if part]
        query = parse_qs(parsed.query, keep_blank_values=False)
        resource_key = self._first_query_value(query, "resourcekey")
        file_id = None

        if (
            len(path_parts) >= 2
            and path_parts[0] == "drive"
            and path_parts[1] == "folders"
        ):
            raise SourceResolutionError(
                f"Google Drive folder URL is not supported in {field_name}"
            )

        if len(path_parts) >= 3 and path_parts[0] == "file" and path_parts[1] == "d":
            file_id = path_parts[2]
        elif len(path_parts) == 1 and path_parts[0] in ("open", "uc"):
            file_id = self._first_query_value(query, "id")

        if not file_id:
            raise SourceResolutionError(
                f"Unsupported Google Drive URL format in {field_name}"
            )

        return GoogleDriveFileRef(
            file_id=file_id,
            resource_key=resource_key,
            original_url=clean_url,
        )

    @staticmethod
    def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        if not values:
            return None
        value = values[0].strip()
        return value or None


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
        self.gdrive_parser = GoogleDriveUrlParser()

    def resolve_primary(self, raw_path: str | None) -> ResolvedSource | None:
        gdrive_ref = self.gdrive_parser.parse(raw_path, "956$u")
        if gdrive_ref:
            return self._resolve_gdrive(gdrive_ref, "956$u")

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
        gdrive_ref = self.gdrive_parser.parse(raw_path, "956$q")
        if gdrive_ref:
            return self._resolve_gdrive(gdrive_ref, "956$q")

        return self.local_source.resolve(
            raw_path,
            "956$q",
            lifecycle_policy="local_unmanaged",
        )

    def resolve_local_path(self, raw_path: str | None, field_name: str) -> str | None:
        resolved = self.local_source.resolve(raw_path, field_name)
        return resolved.local_path if resolved else None

    def _resolve_gdrive(
        self, gdrive_ref: GoogleDriveFileRef, field_name: str
    ) -> ResolvedSource:
        return ResolvedSource(
            local_path="",
            source_type="gdrive",
            original_name=gdrive_ref.file_id,
            temporary=True,
            cleanup_paths=(),
            diagnostics={
                "field_name": field_name,
                "file_id": gdrive_ref.file_id,
                "resource_key": gdrive_ref.resource_key,
                "raw_url": gdrive_ref.original_url,
            },
            lifecycle_policy="remote_ephemeral",
        )
