import os
from src.core import parse_marc_details, run_dspace_workflow, process_integration_logic
from src.tasks import task_manager

# stub implementations
class StubKoha:
    def __init__(self):
        self.metadata = {"file_path": "missing.pdf", "collection_uuid": "coll123"}
        self.status_log = []
    def get_biblio_metadata(self, num):
        return self.metadata
    def _get_biblio_xml(self, num):
        return '<record><datafield tag="856" ind1="4" ind2="0"><subfield code="u">http://example.com/handle/1/2</subfield></datafield></record>'
    def set_status(self, num, status, msg=None):
        self.status_log.append((num, status, msg))
    def set_success(self, num, handle_url, item_uuid=None, cover_url=None):
        self.status_log.append((num, 'imported', handle_url))
    def get_cover_image_url(self, num):
        return "http://koha/cover.jpg"

class StubDSpace:
    def __init__(self):
        self.uploaded = []
    def find_item_by_biblionumber(self, num):
        return None
    def create_item_direct(self, uuid, md):
        return {"uuid": "u1", "handle": "1/2"}
    def upload_to_item(self, item_uuid, path):
        self.uploaded.append((item_uuid, path))
        return True
    def update_metadata(self, item_uuid, md):
        return True
    def find_item_uuid_by_handle(self, handle):
        return None


def test_parse_marc_rules_basic():
    xml = '<record><datafield tag="245" ind1=" " ind2=" "><subfield code="a">Hello</subfield></datafield></record>'
    out = parse_marc_details(xml)
    assert out.get('dc.title') == 'Hello'


def test_run_dspace_with_stubs(tmp_path):
    koha = StubKoha()
    dspace = StubDSpace()
    # simulate existing metadata
    res = run_dspace_workflow(5, str(tmp_path / "file.pdf"), {"collection_uuid": "coll"}, koha_client=koha, dspace_client=dspace)
    assert 'handle' in res and 'uuid' in res
    assert dspace.uploaded[0][0] == 'u1'


def test_task_manager_integration(tmp_path):
    # ensure task_manager propagates kwargs
    koha = StubKoha()
    dspace = StubDSpace()
    # use a file that doesn't exist to force error path
    task_id = task_manager.start_task(process_integration_logic, 9, koha_client=koha, dspace_client=dspace)
    # wait until task finishes (with timeout)
    import time
    deadline = time.time() + 2
    info = None
    while time.time() < deadline:
        info = task_manager.get_status(task_id)
        if info and info['status'] != 'processing':
            break
        time.sleep(0.05)
    assert info is not None
    assert info['status'] in ('error', 'success')
    assert koha.status_log
