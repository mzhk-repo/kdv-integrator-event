#!/usr/bin/env bash
set -euo pipefail

source_path="${COVERS_STORAGE_HOST_PATH:?COVERS_STORAGE_HOST_PATH is required}/assets"
backup_path="${COVER_ASSETS_BACKUP_HOST_PATH:?COVER_ASSETS_BACKUP_HOST_PATH is required}"

[[ "${source_path}" == /* && -d "${source_path}" && ! -L "${source_path}" ]] || {
  echo "Invalid cover assets source directory: ${source_path}" >&2
  exit 1
}
[[ "${backup_path}" == /* && "${backup_path}" != / && ! -L "${backup_path}" ]] || {
  echo "Invalid local assets backup directory: ${backup_path}" >&2
  exit 1
}
[[ "${backup_path}" != "${source_path}" && "${backup_path}" != "${source_path}/"* && "${source_path}" != "${backup_path}/"* ]] || {
  echo "Assets source and backup directories must not overlap" >&2
  exit 1
}

install -d -m 0750 -- "${backup_path}"
backup_fstype="$(findmnt -T "${backup_path}" -n -o FSTYPE)"
[[ "${backup_fstype}" != fuse.rclone && "${backup_fstype}" != rclone ]] || {
  echo "Assets backup destination must be local, not an rclone mount: ${backup_path}" >&2
  exit 1
}
rsync --archive --human-readable --stats -- "${source_path}/" "${backup_path}/"
