class DSpaceClientWrapper:
    """Thin wrapper around the existing DSpaceClient for DI and testing.
    Uses lazy import so tests can run without network/dependency requirements.
    """

    def __init__(self):
        try:
            from ..dspace import DSpaceClient as _DSpaceClient

            self._client = _DSpaceClient()
        except Exception:

            class _Stub:
                def __getattr__(self, name):
                    raise RuntimeError("DSpaceClient not available in this environment")

            self._client = _Stub()

    def __getattr__(self, name):
        return getattr(self._client, name)

    def find_item_by_biblionumber(self, biblionumber):
        return self._client.find_item_by_biblionumber(biblionumber)

    def create_item_direct(self, collection_uuid, metadata_dict):
        return self._client.create_item_direct(collection_uuid, metadata_dict)

    def get_primary_bitstream(self, item_uuid):
        return self._client.get_primary_bitstream(item_uuid)

    def upload_to_item(self, item_uuid, file_path, upload_name=None):
        return self._client.upload_to_item(
            item_uuid, file_path, upload_name=upload_name
        )

    def update_metadata(self, item_uuid, metadata_dict):
        return self._client.update_metadata(item_uuid, metadata_dict)

    def find_item_uuid_by_handle(self, handle):
        return self._client.find_item_uuid_by_handle(handle)

    def get_item_last_modified(self, item_uuid):
        return self._client.get_item_last_modified(item_uuid)
