import os
import shutil
import logging

from ..config import FOLDER_PROCESSED, FOLDER_ERROR

logger = logging.getLogger("KDV-Files")


class FileService:
    """Handle file operations: versioning, rename-first, error moves."""

    def __init__(self, base_mount_path=None):
        # base_mount_path unused currently but may help in tests
        self.base_mount_path = base_mount_path

    def version_and_move(self, original_full_path: str, biblionumber: int) -> str:
        """Move a file into a versioned name under a `Processed` subfolder.

        Returns the new path.
        """
        source_dir = os.path.dirname(original_full_path)
        target_dir = os.path.join(source_dir, FOLDER_PROCESSED)
        os.makedirs(target_dir, exist_ok=True)

        version = 1
        while True:
            filename = f"biblio_{biblionumber}_v{version:02d}.pdf"
            full_path = os.path.join(target_dir, filename)
            if not os.path.exists(full_path):
                break
            version += 1
            if version > 999:
                full_path = os.path.join(
                    target_dir, f"biblio_{biblionumber}_v999_{os.urandom(4).hex()}.pdf"
                )
                break

        logger.info(f"📂 [Files] Moving {original_full_path} -> {full_path}")
        shutil.move(original_full_path, full_path)
        return full_path

    def move_to_error(self, active_path: str):
        """Move current file to an `Error` sibling folder."""
        if not active_path or not os.path.exists(active_path):
            return
        source_dir = os.path.dirname(active_path)
        parent_dir = os.path.dirname(source_dir)
        error_dir = os.path.join(parent_dir, FOLDER_ERROR)
        os.makedirs(error_dir, exist_ok=True)
        filename = os.path.basename(active_path)
        dest = os.path.join(error_dir, filename)
        try:
            shutil.move(active_path, dest)
            logger.info(f"🛑 [Files] Moved to error folder: {dest}")
        except Exception as e:
            logger.error(f"Failed to move file to Error folder: {e}")
