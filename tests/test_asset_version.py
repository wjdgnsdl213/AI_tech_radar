import ast
from pathlib import Path
import os


def test_bridge_updates_invalidate_static_asset_urls(tmp_path):
    # Isolate the real version function from server startup and database imports.
    source = Path('web/server.py').read_text(encoding='utf-8')
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == '_asset_version')
    scope = {'_STATIC': tmp_path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<asset-version>', 'exec'), scope)
    for name in ['app.js', 'preview-bridge.js', 'preview-bridge.css']:
        (tmp_path / name).touch()
        os.utime(tmp_path / name, (100, 100))
    version = scope['_asset_version']()
    for stamp, name in [(200, 'preview-bridge.js'), (300, 'preview-bridge.css')]:
        os.utime(tmp_path / name, (stamp, stamp))
        updated = scope['_asset_version']()
        assert updated != version
        version = updated
