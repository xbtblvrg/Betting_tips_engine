"""Missing native identity evidence cannot be replaced by a completeness flag."""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import load_archive
from test_d20_passer_identity_recovery import native_game
from test_d20_recovered_native import bundle, manifest


@pytest.mark.parametrize('column', ['passer_player_id', 'passer_player_name'])
def test_missing_passer_column_rejects_even_with_synthetic_attestation(
        tmp_path, native_game, column):
    entry, schema, rows = native_game
    table = pa.Table.from_pylist(rows, schema=schema).drop([column])
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    digest = bundle(tmp_path, entry, sink.getvalue().to_pybytes(), len(rows))
    path = tmp_path / 'manifest.json'
    data = json.loads(path.read_bytes())
    data['sources'][0]['sequence_completeness_verified'] = True
    path.write_text(json.dumps(data))
    with pytest.raises(SequenceError, match='^missing_native_columns$'):
        load_archive(tmp_path, digest)
