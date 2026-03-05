class KohaClientWrapper:
    """Thin wrapper around the existing KohaClient.
    Uses lazy import so tests can run without all external dependencies.
    """

    def __init__(self):
        try:
            from ..koha import KohaClient as _KohaClient

            self._client = _KohaClient()
        except Exception:
            # Fallback stub that raises if used; allows tests to import wrapper
            class _Stub:
                def __getattr__(self, name):
                    raise RuntimeError("KohaClient not available in this environment")

            self._client = _Stub()

    def __getattr__(self, name):
        return getattr(self._client, name)

    def get_biblio_metadata(self, biblio_id):
        return self._client.get_biblio_metadata(biblio_id)

    def _get_biblio_xml(self, biblio_id):
        return self._client._get_biblio_xml(biblio_id)

    def set_status(self, biblio_id, status, msg=None):
        return self._client.set_status(biblio_id, status, msg)

    def set_success(self, biblio_id, handle_url, item_uuid=None, cover_url=None):
        return self._client.set_success(
            biblio_id, handle_url, item_uuid=item_uuid, cover_url=cover_url
        )

    def get_cover_image_url(self, biblionumber):
        return self._client.get_cover_image_url(biblionumber)

    def upload_cover(self, biblionumber, file_path):
        return self._client.upload_cover(biblionumber, file_path)

    def check_cover_exists(self, biblionumber):
        return self._client.check_cover_exists(biblionumber)

    def get_biblio_timestamp(self, biblio_id):
        return self._client.get_biblio_timestamp(biblio_id)
