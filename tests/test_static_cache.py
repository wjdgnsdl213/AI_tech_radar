from fastapi import FastAPI
from fastapi.testclient import TestClient
import os


def test_versioned_assets_can_be_reused_but_unversioned_urls_revalidate(tmp_path):
    from web.server import VersionedStaticFiles
    (tmp_path / 'app.js').write_text('const ready = true;', encoding='utf-8')
    app = FastAPI()
    app.mount('/static', VersionedStaticFiles(directory=str(tmp_path)))
    with TestClient(app) as client:
        asset = client.get('/static/app.js?v=release-one')
        assert asset.status_code == 200
        assert asset.headers['cache-control'] == 'public, max-age=3600'
        assert client.get('/static/app.js').headers['cache-control'] == 'no-cache'
        revalidated = client.get('/static/app.js?v=release-one', headers={'If-None-Match': asset.headers['etag']})
        assert revalidated.status_code == 304
        assert revalidated.headers['cache-control'] == 'public, max-age=3600'
        assert client.get('/static/missing.js?v=release-one').status_code == 404


def test_asset_version_tracks_all_scripts_even_with_identical_modification_times(tmp_path, monkeypatch):
    from web import server
    monkeypatch.setattr(server, '_STATIC', tmp_path)
    helper = tmp_path / 'regulatory-loader.js'
    helper.write_text('one', encoding='utf-8')
    os.utime(helper, (1, 1))
    before = server._asset_version()
    helper.write_text('two', encoding='utf-8')
    os.utime(helper, (1, 1))
    assert server._asset_version() != before
    assert server._asset_version() == server._asset_version()
