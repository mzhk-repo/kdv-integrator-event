# Тестовий скелет для клієнтів Koha/DSpace — мокати мережу.


def test_koha_wrapper_imports():
    from src.clients.koha import KohaClientWrapper

    k = KohaClientWrapper()
    assert hasattr(k, "get_biblio_metadata")


def test_dspace_wrapper_imports():
    from src.clients.dspace import DSpaceClientWrapper

    d = DSpaceClientWrapper()
    assert hasattr(d, "create_item_direct")
