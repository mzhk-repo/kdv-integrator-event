import time
import logging
from pathlib import Path
from PIL import Image

# Спробуємо імпортувати pdf2image, якщо бібліотека встановлена
try:
    from pdf2image import convert_from_path

    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# Налаштування логування
logger = logging.getLogger(__name__)


class CoverService:
    """
    Сервіс для генерації обкладинок з PDF та завантаження їх у Koha.
    Реалізує політику безпеки (Retry, Timeout) та стандарти зображень.
    """

    # --- COVER POLICY CONSTANTS ---
    TARGET_WIDTH = 600  # Цільова ширина
    MAX_WIDTH = 800  # Жорсткий ліміт
    JPEG_QUALITY = 80  # Якість стиснення
    DEFAULT_DPI = 150  # Роздільна здатність для Poppler
    GENERATION_TIMEOUT = 15  # Секунд на генерацію (Time Limit)
    MAX_RETRIES = 2  # Спроби читання PDF
    RETRY_DELAY = 1  # Секунд між спробами

    def __init__(self, koha_client=None):
        """
        :param koha_client: Екземпляр клієнта KohaAPI (для перевірки та завантаження)
        """
        self.koha = koha_client
        if not PDF2IMAGE_AVAILABLE:
            logger.warning(
                "⚠️ pdf2image not installed. Cover generation will be disabled."
            )

    def process_book(
        self,
        biblionumber: str,
        pdf_path: str | None,
        output_base_dir: str,
        cover_source_path: str | None = None,
    ):
        """
        Головний метод процесу.
        1. Перевіряє наявність обкладинки в Koha для автогенерації.
        2. Бере готову обкладинку з cover_source_path або генерує файл з PDF.
        3. Завантажує в Koha (якщо клієнт підключено).
        """
        if not cover_source_path and not PDF2IMAGE_AVAILABLE:
            return {"status": "skipped", "reason": "missing_library"}

        # 1. Strict Mode для автогенерації: не перезаписуємо ручну роботу.
        if (
            not cover_source_path
            and self.koha
            and self._check_if_cover_exists(biblionumber)
        ):
            logger.info(
                f"⏭️ [Cover] Skipped for #{biblionumber}: Cover already exists in Koha."
            )
            return {"status": "skipped", "reason": "exists_in_koha"}

        # 2. Підготовка файлу
        try:
            if cover_source_path:
                cover_path = self._prepare_external_cover(
                    biblionumber, cover_source_path, output_base_dir
                )
                logger.info(f"✅ [Cover] Prepared external cover: {cover_path}")
            else:
                cover_path = self._generate_image(biblionumber, pdf_path, output_base_dir)
                logger.info(f"✅ [Cover] Generated: {cover_path}")
        except Exception as e:
            logger.error(f"❌ [Cover] Failed to prepare for #{biblionumber}: {e}")
            return {"status": "error", "reason": str(e)}

        # 3. Завантаження в Koha
        if self.koha:
            upload_success = self._upload_to_koha(biblionumber, cover_path)

            if upload_success:
                logger.info(f"✅ [Cover] Successfully uploaded to Koha #{biblionumber}")
                return {"status": "success", "file": cover_path}
            else:
                logger.warning(f"⚠️ [Cover] Upload returned False for #{biblionumber}")
                return {
                    "status": "warning",
                    "reason": "upload_failed",
                    "file": cover_path,
                }

        return {"status": "generated_only", "file": cover_path}

    def _prepare_external_cover(self, biblionumber, cover_source_path, output_base_dir):
        """
        Готує готову обкладинку до CGI upload.
        JPEG лишаємо як є; інші формати конвертуємо в RGB JPEG.
        """
        source = Path(cover_source_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Cover file not found: {cover_source_path}")

        suffix = source.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return str(source)

        save_dir = Path(output_base_dir) / "covers"
        save_dir.mkdir(parents=True, exist_ok=True)
        full_path = save_dir / f"cover_{biblionumber}_external_v01.jpg"

        with Image.open(source) as image:
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(full_path, "JPEG", quality=self.JPEG_QUALITY, optimize=True)

        return str(full_path)

    def _generate_image(self, biblionumber, pdf_path, output_base_dir):
        """
        Витягує першу сторінку, ресайзить та зберігає.
        Реалізує Retry Policy та Timeout Guard.
        """
        # Створюємо папку для обкладинок
        save_dir = Path(output_base_dir) / "covers"
        save_dir.mkdir(parents=True, exist_ok=True)

        filename = f"cover_{biblionumber}_v01.jpg"
        full_path = save_dir / filename

        # --- EXTRACTION (Stability Guard) ---
        pil_image = None
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                # convert_from_path повертає список зображень
                images = convert_from_path(
                    pdf_path,
                    first_page=1,
                    last_page=1,
                    dpi=self.DEFAULT_DPI,
                    fmt="jpeg",
                    timeout=self.GENERATION_TIMEOUT,  # Poppler timeout guard
                )
                if images:
                    pil_image = images[0]
                    break
            except Exception as e:
                last_error = e
                logger.warning(
                    f"⚠️ [Cover] Attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}"
                )
                time.sleep(self.RETRY_DELAY)

        if not pil_image:
            raise Exception(
                f"Could not extract first page after {self.MAX_RETRIES} retries. Error: {last_error}"
            )

        # --- PROCESSING (Resize) ---
        # Якщо ширина більше ліміту - зменшуємо
        if pil_image.width > self.TARGET_WIDTH:
            w_percent = self.TARGET_WIDTH / float(pil_image.width)
            h_size = int((float(pil_image.height) * float(w_percent)))
            # Використовуємо LANCZOS для якісного зменшення
            pil_image = pil_image.resize(
                (self.TARGET_WIDTH, h_size), Image.Resampling.LANCZOS
            )

        # --- SAVING ---
        pil_image.save(full_path, "JPEG", quality=self.JPEG_QUALITY, optimize=True)

        return str(full_path)

    def _check_if_cover_exists(self, biblionumber):
        """
        Запит до Koha API, щоб перевірити наявність зображення.
        """
        try:
            return self.koha.check_cover_exists(biblionumber)
        except Exception:
            return False

    def _upload_to_koha(self, biblionumber, file_path):
        """
        Завантаження бінарного файлу в Koha.
        """
        try:
            logger.info(f"📡 [Cover] Uploading {file_path} to Koha #{biblionumber}...")
            return self.koha.upload_cover(biblionumber, file_path)
        except Exception as e:
            logger.error(f"❌ [Cover] Upload failed: {e}")
            return False
