import hashlib
import os
import time
from contextlib import suppress
from pathlib import Path
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse


class SourceResolutionError(ValueError):
    pass


class GoogleDriveDownloadError(SourceResolutionError):
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


class GoogleDriveSource:
    DEFAULT_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)

    def __init__(
        self,
        enabled: bool | None = None,
        service_account_file: str | None = None,
        tmp_dir: str | None = None,
        allowed_mime_types: set[str] | None = None,
        max_bytes: int | None = None,
        timeout: int | None = None,
        tmp_ttl_seconds: int | None = None,
        drive_client=None,
    ):
        self.enabled = self._env_bool("GDRIVE_ENABLED") if enabled is None else enabled
        self.service_account_file = service_account_file or os.environ.get(
            "GDRIVE_SERVICE_ACCOUNT_FILE", "/run/secrets/gdrive_service_account_json"
        )
        self.tmp_dir = tmp_dir or os.environ.get(
            "GDRIVE_TMP_DIR", "/data/kdv_sources/gdrive"
        )
        self.allowed_mime_types = allowed_mime_types or self._env_set(
            "GDRIVE_ALLOWED_MIME_TYPES", {"application/pdf"}
        )
        self.max_bytes = max_bytes if max_bytes is not None else int(
            os.environ.get("GDRIVE_MAX_BYTES", "262144000")
        )
        self.timeout = timeout if timeout is not None else int(
            os.environ.get("GDRIVE_DOWNLOAD_TIMEOUT", "300")
        )
        self.tmp_ttl_seconds = tmp_ttl_seconds if tmp_ttl_seconds is not None else int(
            os.environ.get("GDRIVE_TMP_TTL_SECONDS", "86400")
        )
        self.drive_client = drive_client

    def materialize(self, source: ResolvedSource) -> ResolvedSource:
        if source.source_type != "gdrive":
            return source

        if not self.enabled:
            raise GoogleDriveDownloadError("Google Drive source is disabled")
        if not self.drive_client and not os.path.isfile(self.service_account_file):
            raise GoogleDriveDownloadError("Google Drive service account file is missing")

        file_id = source.diagnostics.get("file_id")
        resource_key = source.diagnostics.get("resource_key")
        if not file_id:
            raise GoogleDriveDownloadError("Google Drive file_id is missing")

        client = self.drive_client or self._build_google_drive_client()
        metadata = self._get_metadata(client, file_id, resource_key)
        self._validate_metadata(metadata)

        original_name = self._safe_original_name(metadata.get("name") or file_id)
        self.cleanup_stale_files()
        os.makedirs(self.tmp_dir, exist_ok=True)
        final_path = self._cached_file_path(file_id, resource_key, metadata)
        part_path = f"{final_path}.part"

        if self._is_valid_completed_file(final_path):
            return self._resolved_download(source, final_path, original_name, metadata)

        with suppress(FileNotFoundError):
            os.remove(part_path)

        try:
            self._download_to_file(client, file_id, resource_key, part_path)
            if not self._is_valid_part_file(part_path):
                raise GoogleDriveDownloadError("Google Drive download produced empty file")
            os.replace(part_path, final_path)
        except Exception:
            with suppress(FileNotFoundError):
                os.remove(part_path)
            raise

        return self._resolved_download(source, final_path, original_name, metadata)

    def cleanup_stale_files(self, now: float | None = None) -> list[str]:
        tmp_root = Path(self.tmp_dir).resolve()
        if not tmp_root.exists() or not tmp_root.is_dir():
            return []

        now_ts = time.time() if now is None else now
        deleted = []
        for path in tmp_root.iterdir():
            if not path.is_file() or path.suffix not in {".pdf", ".part"}:
                continue
            try:
                resolved_path = path.resolve()
                if resolved_path.parent != tmp_root:
                    continue
                age_s = now_ts - path.stat().st_mtime
                if age_s < self.tmp_ttl_seconds:
                    continue
                path.unlink()
                deleted.append(str(path))
            except FileNotFoundError:
                continue
            except OSError:
                continue
        return deleted

    def _resolved_download(
        self, source: ResolvedSource, final_path: str, original_name: str, metadata: dict
    ) -> ResolvedSource:
        diagnostics = dict(source.diagnostics)
        diagnostics.update(
            {
                "name": metadata.get("name"),
                "mime_type": metadata.get("mimeType"),
                "size": metadata.get("size"),
            }
        )
        return ResolvedSource(
            local_path=final_path,
            source_type="gdrive",
            original_name=original_name,
            temporary=True,
            cleanup_paths=(final_path,),
            diagnostics=diagnostics,
            lifecycle_policy="remote_ephemeral",
        )

    def _cached_file_path(
        self, file_id: str, resource_key: str | None, metadata: dict
    ) -> str:
        fingerprint = self._metadata_fingerprint(file_id, resource_key, metadata)
        return os.path.join(
            self.tmp_dir,
            f"{self._safe_token(file_id)}-{fingerprint}.pdf",
        )

    @staticmethod
    def _metadata_fingerprint(
        file_id: str, resource_key: str | None, metadata: dict
    ) -> str:
        raw = "|".join(
            [
                file_id,
                resource_key or "",
                str(metadata.get("name") or ""),
                str(metadata.get("mimeType") or ""),
                str(metadata.get("size") or ""),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _is_valid_completed_file(path: str) -> bool:
        return path.endswith(".pdf") and os.path.isfile(path) and os.path.getsize(path) > 0

    @staticmethod
    def _is_valid_part_file(path: str) -> bool:
        return path.endswith(".part") and os.path.isfile(path) and os.path.getsize(path) > 0

    def _build_google_drive_client(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GoogleDriveDownloadError(
                "Google Drive dependencies are not installed"
            ) from exc

        credentials = service_account.Credentials.from_service_account_file(
            self.service_account_file,
            scopes=self.DEFAULT_SCOPES,
        )
        return build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    def _get_metadata(self, client, file_id: str, resource_key: str | None) -> dict:
        if hasattr(client, "get_metadata"):
            return client.get_metadata(file_id=file_id, resource_key=resource_key)

        params = {
            "fileId": file_id,
            "fields": "id,name,mimeType,size,capabilities/canDownload",
            "supportsAllDrives": True,
        }
        if resource_key:
            params["resourceKey"] = resource_key
        return client.files().get(**params).execute()

    def _download_to_file(
        self, client, file_id: str, resource_key: str | None, part_path: str
    ) -> None:
        if hasattr(client, "download_to_file"):
            client.download_to_file(
                file_id=file_id,
                resource_key=resource_key,
                destination_path=part_path,
                timeout=self.timeout,
            )
            return

        try:
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError as exc:
            raise GoogleDriveDownloadError(
                "Google Drive dependencies are not installed"
            ) from exc

        params = {"fileId": file_id, "supportsAllDrives": True}
        if resource_key:
            params["resourceKey"] = resource_key
        request = client.files().get_media(**params)
        with open(part_path, "wb") as stream:
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _status, done = downloader.next_chunk()

    def _validate_metadata(self, metadata: dict) -> None:
        mime_type = metadata.get("mimeType")
        if mime_type not in self.allowed_mime_types:
            raise GoogleDriveDownloadError("Google Drive file mime type is not allowed")

        can_download = metadata.get("capabilities", {}).get("canDownload", True)
        if can_download is False:
            raise GoogleDriveDownloadError("Google Drive file cannot be downloaded")

        raw_size = metadata.get("size")
        if raw_size not in (None, "") and int(raw_size) > self.max_bytes:
            raise GoogleDriveDownloadError("Google Drive file is too large")

    @staticmethod
    def _env_bool(key: str) -> bool:
        return os.environ.get(key, "false").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_set(key: str, default: set[str]) -> set[str]:
        raw = os.environ.get(key, "").strip()
        if not raw:
            return default
        return {part.strip() for part in raw.split(",") if part.strip()}

    @staticmethod
    def _safe_token(value: str) -> str:
        return "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
            for ch in value
        )

    @classmethod
    def _safe_original_name(cls, value: str) -> str:
        name = os.path.basename(value.strip()) or "download.pdf"
        safe_name = cls._safe_token(name)
        if not safe_name.lower().endswith(".pdf"):
            safe_name = f"{safe_name}.pdf"
        return safe_name


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
    def __init__(
        self, base_mount_path: str, gdrive_source: GoogleDriveSource | None = None
    ):
        self.local_source = LocalMountSource(base_mount_path)
        self.gdrive_parser = GoogleDriveUrlParser()
        self.gdrive_source = gdrive_source or GoogleDriveSource()

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

    def materialize(self, source: ResolvedSource | None) -> ResolvedSource | None:
        if source is None:
            return None
        if source.source_type == "gdrive":
            return self.gdrive_source.materialize(source)
        return source

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
