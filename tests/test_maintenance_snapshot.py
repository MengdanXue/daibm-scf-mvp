from copy import deepcopy

from scripts.maintenance_snapshot import compare_history


def evidence():
    return dict(columns={'ledger_events': ['id', 'event_hash']},
        tables={'ledger_events': {'rows': {'[1]': 'sealed-a'}}},
        sequences={'ledger_events_id_seq': dict(last_value=1, is_called=True, increment_by=1)},
        ledger={'valid': True, 'head_hash': 'head-a'})


def test_historical_rows_are_checked_even_when_new_rows_are_allowed():
    before = evidence()
    after = deepcopy(before)
    after['tables']['ledger_events']['rows']['[2]'] = 'sealed-b'
    after['sequences']['ledger_events_id_seq']['last_value'] = 2
    after['ledger']['head_hash'] = 'head-b'
    assert not compare_history(before, after, allow_new_rows=True, allow_sequence_advance=True)
    after['tables']['ledger_events']['rows']['[1]'] = 'tampered'
    assert 'historical row changed' in ';'.join(compare_history(
        before, after, allow_new_rows=True, allow_sequence_advance=True))


def test_migration_cannot_change_called_sequence_flag_or_append_rows():
    before = evidence()
    after = deepcopy(before)
    after['sequences']['ledger_events_id_seq']['is_called'] = False
    assert 'sequence state changed' in ';'.join(compare_history(before, after))
    after = deepcopy(before)
    after['tables']['ledger_events']['rows']['[2]'] = 'new'
    assert 'row set changed' in ';'.join(compare_history(before, after))


def test_ledger_failure_and_missing_original_columns_fail():
    before = evidence()
    after = deepcopy(before)
    after['columns']['ledger_events'] = ['id']
    after['ledger']['valid'] = False
    errors = compare_history(before, after)
    assert len(errors) == 2
