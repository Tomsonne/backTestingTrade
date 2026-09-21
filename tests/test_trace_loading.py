from copy import deepcopy
from datetime import date
import logging

import pandas as pd
import pytest

from app.config import Settings
from app.data.base import DataCoverageError, DataIntegrityError, InvalidValidationPolicy
from app.data.dukascopy import DukascopyProvider
from app.data.gaps import TraceProvider
from app.data.validation import VALIDATION_RUN_ID, validate_market_data
from app.research.engine import run_research_backtest
from app.research.presets import builtin_presets
from test_research_engine import canonical_prices, custom_config


def dataset():
    index = pd.date_range('2024-01-02T07:00Z', periods=10, freq='min')
    return canonical_prices(index.delete([2, 3])), index[0], index[-1] + pd.Timedelta(minutes=1)


def provider_for(tmp_path, monkeypatch, frame, mode='strict'):
    provider = DukascopyProvider(tmp_path, validation_mode=mode)
    monkeypatch.setattr(provider.store, 'read', lambda symbol, start, end: frame[(frame.index >= start) & (frame.index < end)])
    monkeypatch.setattr(provider, '_metadata', lambda *args, **kwargs: {})
    return provider


@pytest.mark.parametrize('symbol', ['EUR_USD', 'GBP_USD', 'DXY', 'AUD_USD'])
def should_return_available_data_when_trace_has_missing_m1_candles(tmp_path, monkeypatch, symbol):
    frame, start, end = dataset()
    provider = provider_for(tmp_path, monkeypatch, frame)
    actual = provider.get(symbol, start, end, '1min', validation_policy='TRACE')
    pd.testing.assert_frame_equal(actual, frame)
    report = provider.validation_reports[symbol]
    assert report.unexpected_missing_minutes == 2
    assert report.expected_market_minutes == 10
    assert report.available_market_minutes == 8
    assert report.coverage_percent == 80
    assert provider.validation_mode == 'strict'  # per-call override is not global


def should_raise_data_coverage_error_when_strict_has_missing_m1_candles(tmp_path, monkeypatch):
    frame, start, end = dataset()
    provider = provider_for(tmp_path, monkeypatch, frame)
    with pytest.raises(DataCoverageError, match='2 missing M1'):
        provider.get('EUR_USD', start, end, '1min', validation_policy='STRICT')
    assert provider.validation_reports['EUR_USD'].unexpected_missing_minutes == 2


def should_continue_research_engine_when_trace_provider_reports_gaps(tmp_path, monkeypatch, caplog):
    frame, _, _ = dataset()
    provider = provider_for(tmp_path, monkeypatch, frame)
    config = custom_config(); config.validation.mode = 'trace'; config.divergence.enabled = True
    config.instruments.pairs = ['EUR_USD', 'GBP_USD']
    stages = []
    with caplog.at_level(logging.INFO):
        output = run_research_backtest(config, Settings(data_dir=tmp_path, db_path=tmp_path/'lab.sqlite3'), provider,
                                      progress=lambda step, _: stages.append(step))
    assert 'GENERATING_SETUPS' in stages and 'CALCULATING_STATISTICS' in stages
    assert set(output.result['coverage']) == {'EUR_USD', 'GBP_USD', 'DXY'}
    assert all(c['missing_market_minutes'] > 0 for c in output.result['coverage'].values())
    assert 'Validation policy: TRACE' in caplog.text
    assert 'Coverage validation: NON-BLOCKING' in caplog.text
    assert 'Gap catalogue: created' in caplog.text
    assert 'Backtest: continued' in caplog.text
    assert 'Run historical data repair first' not in caplog.text
    assert provider.validation_mode == 'strict'
    assert len([s for s in output.stats if s['dimension'] == 'coverage']) == 3


@pytest.mark.parametrize('defect', ['ohlc', 'nan', 'naive', 'nat', 'order', 'duplicate', 'format'])
def should_abort_integrity_error_when_trace_has_corrupt_data(tmp_path, monkeypatch, defect):
    frame, start, end = dataset()
    if defect == 'ohlc': frame.iloc[0, frame.columns.get_loc('mid_l')] = 200
    if defect == 'nan': frame.iloc[0, frame.columns.get_loc('bid_h')] = float('nan')
    if defect == 'naive': frame.index = frame.index.tz_localize(None)
    if defect == 'nat': frame.index = pd.DatetimeIndex([pd.NaT, *frame.index[1:]])
    if defect == 'order': frame = frame.iloc[::-1]
    if defect == 'duplicate': frame = pd.concat([frame, frame.iloc[:1]])
    if defect == 'format': frame = pd.DataFrame({'unknown': 1}, index=frame.index)
    provider = DukascopyProvider(tmp_path, validation_mode='trace')
    monkeypatch.setattr(provider.store, 'read', lambda *args: frame)
    monkeypatch.setattr(provider, '_metadata', lambda *args, **kwargs: {})
    with pytest.raises(DataIntegrityError):
        provider.get('EUR_USD', start, end)


def should_preserve_permissive_behavior_when_data_is_incomplete_and_invalid(tmp_path, monkeypatch):
    frame, start, end = dataset(); frame.iloc[0, frame.columns.get_loc('bid_l')] = 200
    provider = provider_for(tmp_path, monkeypatch, frame, 'permissive')
    pd.testing.assert_frame_equal(provider.get('EUR_USD', start, end), frame)


def should_reject_unknown_policy_when_provider_would_otherwise_accept_data(tmp_path):
    with pytest.raises(InvalidValidationPolicy): DukascopyProvider(tmp_path, validation_mode='typo')
    provider = DukascopyProvider(tmp_path)
    with pytest.raises(InvalidValidationPolicy): provider.get('EUR_USD', '2024-01-02', '2024-01-03', validation_policy='typo')


def should_catalogue_absent_months_when_real_cache_starts_later(tmp_path, monkeypatch):
    frame, _, _ = dataset()
    provider = TraceProvider(provider_for(tmp_path, monkeypatch, frame))
    actual = provider.get('EUR_USD', pd.Timestamp('2023-10-01T00:00Z'), pd.Timestamp('2024-01-03T00:00Z'))
    coverage = provider.catalog.coverage['EUR_USD']
    assert coverage['fully_missing_requested_months_utc'] == ['2023-10', '2023-11', '2023-12']
    assert coverage['missing_before_first_available'] > 80000
    assert coverage['fully_missing_requested_days_utc']
    assert coverage['fully_missing_requested_weeks_utc']
    pd.testing.assert_frame_equal(actual, frame)


def should_return_empty_real_data_when_trace_has_no_candles(tmp_path, monkeypatch):
    frame, start, end = dataset(); frame = frame.iloc[:0]
    provider = TraceProvider(provider_for(tmp_path, monkeypatch, frame))
    assert provider.get('EUR_USD', start, end).empty
    coverage = provider.catalog.coverage['EUR_USD']
    assert coverage['coverage_percent'] == 0
    assert coverage['missing_market_minutes'] == 10
    assert coverage['first_available_candle'] is None


def should_apply_trace_to_each_synthetic_dxy_component_when_fallback_is_selected(tmp_path, monkeypatch):
    from app.dxy import DXY_COMPONENTS
    frame, _, _ = dataset()
    provider = provider_for(tmp_path, monkeypatch, frame)
    config = custom_config(); config.validation.mode = 'trace'; config.divergence.enabled = True
    config.instruments.dxy_source = 'synthetic'
    result = run_research_backtest(config, Settings(data_dir=tmp_path, db_path=tmp_path/'x.db'), provider)
    assert set(result.result['coverage']) == set(DXY_COMPONENTS)


def should_not_fallback_to_synthetic_when_direct_dxy_is_corrupt(tmp_path, monkeypatch):
    frame, _, _ = dataset(); bad = frame.copy(); bad['mid_l'] = 200
    provider = provider_for(tmp_path, monkeypatch, frame)
    loaded = []
    def read(symbol, start, end):
        loaded.append(symbol)
        return bad if symbol == 'DXY' else frame
    monkeypatch.setattr(provider.store, 'read', read)
    config = custom_config(); config.validation.mode = 'trace'; config.divergence.enabled = True
    with pytest.raises(DataIntegrityError):
        run_research_backtest(config, Settings(data_dir=tmp_path, db_path=tmp_path/'x.db'), provider)
    assert loaded == ['EUR_USD', 'DXY']


def should_preserve_trade_results_when_trace_data_has_no_gaps(tmp_path, monkeypatch):
    index = pd.date_range('2024-01-01T23:00Z', '2024-01-02T23:00Z', inclusive='left', freq='min')
    frame = canonical_prices(index)
    for prefix in ('bid', 'ask', 'mid'):
        frame.loc[index >= pd.Timestamp('2024-01-02T12:01Z'), prefix + '_h'] = 1.102
    provider = provider_for(tmp_path, monkeypatch, frame)
    config = custom_config(); settings = Settings(data_dir=tmp_path, db_path=tmp_path/'x.db')
    strict = run_research_backtest(config, settings, provider)
    config.validation.mode = 'trace'
    traced = run_research_backtest(config, settings, provider)
    from app.storage import _json
    assert strict.trades and _json(strict.trades) == _json(traced.trades)
    assert strict.result['summary'] == traced.result['summary']


def should_accept_only_known_policies_when_config_is_validated():
    from app.research.config import StrategyConfig
    config = custom_config().model_dump(mode='json')
    config['validation']['mode'] = 'trace'
    assert StrategyConfig.model_validate(config).validation.mode == 'trace'
    config['validation']['mode'] = 'typo'
    with pytest.raises(ValueError): StrategyConfig.model_validate(config)
