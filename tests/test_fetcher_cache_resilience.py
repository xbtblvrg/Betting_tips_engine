import time
import json
import hashlib
from pathlib import Path
import httpx
from src.betting_engine.providers import Fetcher

def test_fetcher_recovers_gracefully_when_blob_missing(tmp_path):
    f = Fetcher(root=tmp_path)
    url = "https://example.com/test_data"
    key = hashlib.sha256(url.encode()).hexdigest()
    m_path = tmp_path / "raw" / (key + ".json")
    
    # Simulate an orphaned metadata JSON pointing to a missing .data blob
    m_path.parent.mkdir(parents=True, exist_ok=True)
    m_path.write_text(json.dumps({
        "url": url,
        "epoch": time.time(),
        "sha256": "deadbeef",
        "blob": "deadbeef.data",
        "http_status": 200
    }))
    
    # Mock client.get to return fake bytes
    def mock_get(req_url):
        class MockResponse:
            status_code = 200
            content = b"fresh_network_data"
            def raise_for_status(self): pass
        return MockResponse()

    f.client.get = mock_get
    
    # Must NOT raise FileNotFoundError; must fall through and fetch fresh bytes
    data, meta = f.get(url, ttl=3600)
    assert data == b"fresh_network_data"
    assert (tmp_path / "raw" / meta["blob"]).is_file()
