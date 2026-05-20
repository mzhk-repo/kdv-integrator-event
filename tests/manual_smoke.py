"""Simple smoke test script for core logic without pytest dependency."""

from src.core import process_integration_logic, run_dspace_workflow, parse_marc_details


# stub clients
class StubKoha:
    def __init__(self):
        self.metadata = {"file_path": "nonexistent.pdf", "collection_uuid": "coll-123"}

    def get_biblio_metadata(self, biblio):
        return self.metadata

    def _get_biblio_xml(self, biblio):
        # minimal marc xml with 856 field containing handle
        return '<record><datafield tag="856" ind1="4" ind2="0"><subfield code="u">http://example.com/handle/123/456</subfield></datafield></record>'

    def set_status(self, biblio, status, msg=None):
        print(f"Koha.set_status called {biblio} {status} {msg}")

    def set_success(self, biblio, handle_url, item_uuid=None, cover_url=None):
        print(f"Koha.set_success {biblio} {handle_url} {item_uuid} {cover_url}")

    def get_cover_image_url(self, biblionumber):
        return "http://koha.cover/image.jpg"


class StubDSpace:
    def __init__(self):
        self.created = False

    def find_item_by_biblionumber(self, num):
        return None

    def create_item_direct(self, coll, md):
        print("DSpace.create_item_direct", coll, md)
        self.created = True
        return {"uuid": "uuid-1", "handle": "123/456"}

    def upload_to_item(self, item_uuid, file_path, upload_name=None):
        print("DSpace.upload_to_item", item_uuid, file_path, upload_name)
        return True


# test parse_marc
print(
    "parse_marc_details output",
    parse_marc_details(
        '<record><datafield tag="245" ind1=" " ind2=" "><subfield code="a">Title</subfield></datafield></record>'
    ),
)

# test run_dspace_workflow
print(
    run_dspace_workflow(
        1,
        "/tmp/fake.pdf",
        {"collection_uuid": "col"},
        koha_client=StubKoha(),
        dspace_client=StubDSpace(),
    )
)

# test process_integration_logic with missing file should set error and raise
try:
    process_integration_logic(
        "task1", 1, koha_client=StubKoha(), dspace_client=StubDSpace()
    )
except Exception as e:
    print("Expected exception from missing file", e)

print("smoke script finished")
