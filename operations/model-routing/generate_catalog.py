#!/usr/bin/env python3
"""Build evidence, not routing policy. No inference requests or credential reads.

Run with --refresh-public for unauthenticated /models metadata GETs. All other
inputs are local, read-only snapshots. SQLite uses mode=ro and query_only.
--benchmark accepts JSON {rows:[{provider,model,task_class,benchmark,score,
unit,source,observed_at,floor,floor_source,floor_observed_at}]}. A score is
not p_accept. A floor without its own source and timestamp is rejected.
Unknown values remain null. This module never imports or replaces Kerdoios.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import urllib.request

OPS = Path('/mnt/zer0models/workspace/zer0/data/operations')
HERMES = Path('/workspace/hermes-home/profiles/chiefstaff')
PROVIDERS = ('opencode-zen', 'opencode-go', 'nous', 'openrouter', 'vercel', 'cerebras', 'groq', 'openai-codex', 'anthropic')
PUBLIC = {'vercel': 'https://ai-gateway.vercel.sh/v1/models',
          'openrouter': 'https://openrouter.ai/api/v1/models',
          'opencode-zen': 'https://opencode.ai/zen/v1/models'}


def timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:
            return None
    if value > 10**11:
        value /= 1000
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def provider_name(value):
    value = str(value or '')
    if value.startswith('openai-codex:'):
        return 'openai-codex'
    if value.startswith('opencode-zen:') or value in ('opencode', 'opencode-free'):
        return 'opencode-zen'
    if value.startswith('opencode-go:'):
        return 'opencode-go'
    return {'ai-gateway': 'vercel', 'vercel-ai-gateway': 'vercel'}.get(value, value)


def metric(value=None, unit=None, source=None, at=None, basis='unknown', reason=None):
    return {'value': value, 'unit': unit, 'source': source, 'observed_at': at,
            'basis': basis, **({'reason': reason} if reason else {})}


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


class Catalog:
    def __init__(self, now):
        self.now = now
        self.at = timestamp(now)
        self.rows = {}
        self.sources = {}
        self.issues = []
        self.pools = []
        self.benchmark_sources = []
        self.constraints = {}
        self.doc_refs = defaultdict(list)
        self.limit_rules = []

    def documentation(self, path, needle):
        source = self.source(path, 'documentation')
        try:
            lines = Path(path).expanduser().read_text().splitlines()
        except OSError:
            self.issues.append({'source': source, 'issue': 'documentation_unavailable'})
            return None
        for line_number, text in enumerate(lines, 1):
            if needle in text:
                key = source + ':' + str(line_number)
                self.sources[key] = {**self.sources[source], 'line': line_number,
                                     'citation': f'{Path(path).expanduser()}:{line_number}'}
                return key
        self.issues.append({'source': source, 'issue': 'documented_section_not_found'})
        return None

    def documented_number(self, source, pattern):
        if source is None:
            return None
        entry = self.sources[source]
        text = Path(entry['path']).read_text().splitlines()[entry['line'] - 1]
        match = re.search(pattern, text)
        return number(match.group(1)) if match else None

    def seed_documentation(self, args):
        """Retain existing documented policy before refreshing model metadata."""
        provider_sections = {
            'cerebras': '| Cerebras |', 'groq': '| Groq |',
            'openrouter': '| OpenRouter `:free` models |',
            'vercel': '| Vercel AI Gateway |', 'opencode-zen': '| OpenCode Zen |'}
        for provider in PROVIDERS:
            auth_needle = f'| {provider} |' if provider in ('nous', 'anthropic', 'openai-codex', 'openrouter') else '| fireworks /'
            auth_source = self.documentation(args.provider_guidance, auth_needle)
            doc_source = self.documentation(args.free_tier_docs, provider_sections[provider]) if provider in provider_sections else None
            refs = [ref for ref in (auth_source, doc_source) if ref]
            self.doc_refs[provider].extend(refs)
            self.constraints[provider] = {
                'documentation': [self.sources[ref]['citation'] for ref in refs],
                'free_status': metric(reason='Provider plan or exact-model free eligibility is unconfirmed'),
                'privacy': metric(reason='No general privacy permission established by inspected sources'),
                'terms_sources': refs,
                'model_catalog_is_entitlement': False}
        source = self.documentation(args.free_tier_docs, '| Cerebras |')
        if source:
            at = self.sources[source]['snapshot_at']
            self.constraints['cerebras'].update({
                'billing_class': metric('trial_credit_or_paid_not_recurring_free', 'class', source, at, 'documented_terms'),
                'free_status': metric(False, 'boolean', source, at, 'no_permanent_free_tier'),
                'trial_credit_usd': metric(self.documented_number(source, r'\$([\d.]+)'), 'USD', source, at, 'documented_trial_not_current_balance'),
                'trial_expiry_days': metric(self.documented_number(source, r'after (\d+) days'), 'days', source, at, 'documented_trial_duration'),
                'payment_method_required': metric(True, 'boolean', source, at, 'documented_terms')})
        source = self.documentation(args.free_tier_docs, '| Vercel AI Gateway |')
        if source:
            at = self.sources[source]['snapshot_at']
            self.constraints['vercel'].update({
                'billing_class': metric('recurring_credit_eligible_subset_only', 'class', source, at, 'documented_terms'),
                'monthly_credit_usd': metric(self.documented_number(source, r'\$([\d.]+)/month'), 'USD/month', source, at, 'documented_allowance_not_balance'),
                'model_free_eligibility': metric(reason='Full model catalog does not identify free-tier subset'),
                'purchased_credits_end_monthly_allowance': metric(True, 'boolean', source, at, 'documented_terms')})
        # No numeric Go/DeepSeek daily ceiling occurs in the named retained docs.
        source = self.documentation(args.provider_guidance, '| fireworks /')
        if source:
            self.constraints['opencode-go']['documented_model_constraints'] = [{
                'model_family': 'DeepSeek', 'dimension': 'rpd',
                'limit': metric(unit='requests/day', source=source,
                    at=self.sources[source]['snapshot_at'], reason='Go provider is documented; no DeepSeek daily ceiling or exact free Go model ID in these sources'),
                'remaining': metric(unit='requests/day', reason='No account quota snapshot'),
                'reset_at': metric(unit='ISO8601', reason='No observed reset'),
                'scope': 'daily_quota_constraint_not_a_retry_delay',
                'documentation': [self.sources[source]['citation']]}]
        data, source = self.load(args.free_tier_catalog, 'retained_free_tier_catalog')
        at = (data or {}).get('read_on')
        for offering in (data or {}).get('llm_apis', []):
            provider = provider_name(offering.get('provider'))
            if provider not in PROVIDERS:
                continue
            doc = self.documentation(args.free_tier_catalog, '"provider": "' + offering['provider'] + '"')
            if not doc:
                continue
            self.doc_refs[provider].append(doc)
            constraint = self.constraints[provider]
            constraint['documentation'].append(self.sources[doc]['citation'])
            constraint['privacy'] = metric(offering.get('data_policy'), 'policy', doc, at, 'retained_documented_policy_not_authorization')
            constraint['terms'] = metric(offering.get('terms'), 'text', doc, at, 'retained_documented_terms')
            constraint['gotchas'] = metric(offering.get('gotchas'), 'text', doc, at, 'retained_documented_caveats')
            for model in offering.get('models', []):
                model_id = model.get('model')
                if not model_id:
                    continue
                model_source = self.documentation(args.free_tier_catalog, '"model": "' + model_id + '"') or doc
                self.limit_rules.append({'provider': provider, 'model': model_id,
                    'source': model_source, 'at': at, 'limits': model,
                    'tier': model.get('tier'), 'scope': 'published_plan_not_account_entitlement'})
                if provider == 'openrouter' and model_id == '*:free':
                    self.limit_rules[-1]['tier'] = 'credits_purchased_below_documented_threshold'
                    evidence = Path(args.free_tier_catalog).parent / 'evidence/openrouter_limits.md'
                    tier_source = self.documentation(evidence, '| At least ')
                    if tier_source:
                        high_daily = self.documented_number(tier_source, r'\|\s*At least \d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|')
                        high_rpm = self.documented_number(tier_source, r'\|\s*At least \d+\s*\|\s*(\d+)\s*\|')
                        self.limit_rules.append({'provider': provider, 'model': model_id,
                            'source': tier_source, 'at': at,
                            'limits': {'rpm': high_rpm, 'rpd': high_daily,
                                       'note': 'Conditional on all-time purchased credits; current account tier unknown'},
                            'tier': 'credits_purchased_at_least_documented_threshold',
                            'scope': 'published_plan_not_account_entitlement'})
                if '*' not in model_id:
                    row = self.row(provider, model_id)
                    self.identity(row, model_source, at, 'retained_documented_model')
                    if provider == 'opencode-zen' and offering.get('free') is True:
                        row['free_status'] = metric(True, 'boolean', model_source, at, 'documented_free_model',
                                                   'Dated free-model documentation, not current entitlement proof')

    def source(self, path, kind):
        path = Path(path).expanduser().resolve()
        key = kind + ':' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        if key not in self.sources:
            self.sources[key] = {'path': str(path), 'kind': kind, 'captured_at': self.at,
                                 'snapshot_at': timestamp(path.stat().st_mtime) if path.exists() else None,
                                 'status': 'present' if path.exists() else 'missing'}
        return key

    def load(self, path, kind):
        source = self.source(path, kind)
        try:
            with Path(path).expanduser().open() as stream:
                return json.load(stream), source
        except (OSError, ValueError):
            self.issues.append({'source': source, 'issue': 'unavailable_or_invalid_json'})
            return None, source

    def connect(self, path, kind):
        source = self.source(path, kind)
        try:
            connection = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True)
            connection.execute('PRAGMA query_only=ON')
            connection.row_factory = sqlite3.Row
            return connection, source
        except sqlite3.Error:
            self.issues.append({'source': source, 'issue': 'read_only_sqlite_unavailable'})
            return None, source

    def row(self, provider, model):
        provider = provider_name(provider)
        if provider not in PROVIDERS or not model:
            return None
        key = (provider, model)
        if key not in self.rows:
            self.rows[key] = {'provider': provider, 'model': model, 'identity_sources': [],
                'availability': 'catalog_or_historical_only_not_entitlement_proof',
                'pricing': {name: metric(unit='USD_per_million_tokens', reason='No price evidence')
                            for name in ('input', 'output', 'cache_read', 'cache_write')},
                'performance': {name: metric(unit=unit, reason='No measured field') for name, unit in
                    (('ttft_p50_ms', 'ms'), ('ttft_p95_ms', 'ms'), ('ttft_mean_ms', 'ms'),
                     ('tokens_per_second', 'output_tokens/s'), ('mean_output_tokens', 'output_tokens'))},
                'burn': {'spend_usd_per_hour': metric(unit='USD/h', reason='No actual billed spend interval'),
                         'quota_fraction_per_hour': metric(unit='fraction/h', reason='Account pool, not attributable to model'),
                         'tokens_per_hour': metric(unit='tokens/h', reason='No complete model-attributed interval'),
                         'remaining': metric(reason='See shared quota pools; no model-specific balance'),
                         'time_to_reset_seconds': metric(unit='s', reason='See shared quota pools')},
                'quota_pool_ids': [], 'quota_windows': [], 'task_classes': {}, 'benchmarks': [],
                'capability_floor': metric(reason='No owner-approved benchmark floor supplied')}
        return self.rows[key]

    def identity(self, row, source, at, basis):
        if row is not None:
            item = {'source': source, 'observed_at': at, 'basis': basis}
            if item not in row['identity_sources']:
                row['identity_sources'].append(item)

    def prices(self, row, costs, source, at, multiplier=1, public=False):
        if row is None:
            return
        for name, aliases in {'input': ('input', 'prompt'), 'output': ('output', 'completion'),
                              'cache_read': ('cacheRead', 'input_cache_read', 'cache_read'),
                              'cache_write': ('cacheWrite', 'input_cache_write', 'cache_write')}.items():
            value = next((number(costs[k]) for k in aliases if k in costs), None)
            if value is not None:
                row['pricing'][name] = metric(value * multiplier, 'USD_per_million_tokens', source, at,
                                              'public_list_price' if public else 'cached_list_price',
                                              'Not actual spend or subscription marginal cost')
        if row['provider'] in ('opencode-zen', 'opencode-go'):
            input_price = row['pricing']['input']
            output_price = row['pricing']['output']
            if input_price['value'] == 0 and output_price['value'] == 0:
                row['free_status'] = metric(True, 'boolean', source, at,
                    'explicit_zero_input_output_list_price',
                    'Observed free pricing, not current quota or account entitlement proof')
            elif any(value['value'] is not None and value['value'] > 0
                     for value in (input_price, output_price)):
                row['free_status'] = metric(False, 'boolean', source, at,
                    'explicit_nonzero_list_price')
        # Retain tiers as an evidence object. Its numeric contents share this provenance.
        tiers = {k: v for k, v in costs.items() if k.endswith('_tiers') or k in ('longContext', 'service_tiers', 'fast', 'regional', 'varies_by_provider')}
        if tiers:
            row['pricing']['conditions'] = metric(tiers, 'source_native_price_units', source, at,
                                                 'public_list_price' if public else 'cached_list_price')

    def model_metadata(self, args):
        data, source = self.load(args.provider_cache, 'hermes_provider_cache')
        for raw_provider, payload in (data or {}).items():
            provider = provider_name(raw_provider)
            if provider not in PROVIDERS or raw_provider.startswith('custom:'):
                continue
            for model in payload.get('models', []):
                row = self.row(provider, model)
                self.identity(row, source, timestamp(payload.get('at')), 'cached_served_id')
                if raw_provider == 'opencode-free' and row is not None:
                    row['free_status'] = metric(True, 'boolean', source, timestamp(payload.get('at')),
                        'explicit_free_provider_catalog',
                        'Free-only cached provider catalog; not current quota or account entitlement proof')
        connection, source = self.connect(args.omp_models_db, 'omp_model_cache')
        if connection:
            try:
                for item in connection.execute('SELECT provider_id,updated_at,models FROM model_cache ORDER BY updated_at'):
                    provider = provider_name(item['provider_id'])
                    if provider not in PROVIDERS:
                        continue
                    at = timestamp(item['updated_at'])
                    for model in json.loads(item['models']):
                        row = self.row(provider, model.get('id'))
                        self.identity(row, source, at, 'cached_model_metadata')
                        self.prices(row, model.get('cost') or {}, source, at)
            except (sqlite3.Error, ValueError, TypeError):
                self.issues.append({'source': source, 'issue': 'unsupported_model_cache_schema'})
            finally:
                connection.close()
        data, source = self.load(args.inventory, 'kerdoios_inventory')
        for offer in (data or {}).get('offers', []):
            provider = offer.get('transport') or ('openrouter' if str(offer.get('source', '')).startswith('openrouter:') else offer.get('provider'))
            row = self.row(provider, offer.get('model'))
            self.identity(row, source, timestamp(data.get('saved_at')), 'cached_offer')
        self.issues.append({'source': source, 'issue': 'Kerdoios capability priors and telemetry defaults are not measured benchmarks or TTFT; not imported'})
        data, source = self.load(args.nous_metadata, 'nous_endpoint_metadata')
        for payload in (data or {}).values():
            for model, metadata in payload.get('models', {}).items():
                row = self.row('nous', model)
                at = timestamp(payload.get('at'))
                self.identity(row, source, at, 'cached_endpoint_model_metadata')
                self.prices(row, metadata.get('pricing') or {}, source, at, 1_000_000)

    def public_metadata(self, args):
        payloads = []
        for specification in args.public_models:
            provider, path = specification.split('=', 1)
            data, source = self.load(path, 'public_models_' + provider_name(provider))
            payloads.append((provider_name(provider), data, source, self.sources[source]['snapshot_at']))
        if args.refresh_public:
            for provider, url in PUBLIC.items():
                source = 'public:' + provider
                self.sources[source] = {'url': url, 'kind': 'public_models_metadata', 'captured_at': self.at,
                                        'snapshot_at': self.at, 'status': 'unavailable'}
                try:
                    request = urllib.request.Request(url, headers={'User-Agent': 'model-routing-catalog/1'})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        data = json.load(response)
                    self.sources[source]['status'] = 'present'
                    payloads.append((provider, data, source, self.at))
                except (OSError, ValueError):
                    self.issues.append({'source': source, 'issue': 'public_metadata_GET_failed_no_inference_attempted'})
        for provider, payload, source, at in payloads:
            data = (payload or {}).get('data', [])
            for model in data:
                row = self.row(provider, model.get('id'))
                self.identity(row, source, at, 'public_served_id_not_account_entitlement')
                self.prices(row, model.get('pricing') or {}, source, at, 1_000_000, public=True)

    def omp(self, args):
        connection, source = self.connect(args.omp_db, 'omp_agent_db')
        if not connection:
            return
        try:
            for item in connection.execute('SELECT * FROM model_perf'):
                provider, sep, model = item['model_key'].partition('/')
                row = self.row(provider, model) if sep else None
                if row is None:
                    continue
                at = timestamp(item['updated_at'])
                self.identity(row, source, at, 'model_perf_observation')
                perf = row['performance']
                if item['ttft_samples'] > 0:
                    perf['ttft_mean_ms'] = metric(item['ttft_ms'] / item['ttft_samples'], 'ms', source, at,
                                                  'aggregate_ratio_not_quantile')
                if item['gen_ms'] > 0:
                    perf['tokens_per_second'] = metric(item['output_tokens'] * 1000 / item['gen_ms'],
                        'output_tokens/s', source, at, 'aggregate_output_tokens_over_gen_ms',
                        'OMP aggregate may be decayed; not per-request median decode speed')
                if item['samples'] > 0:
                    perf['mean_output_tokens'] = metric(item['output_tokens'] / item['samples'],
                        'output_tokens', source, at, 'aggregate_ratio_not_task_prediction')
                perf['aggregate_samples'] = metric(item['samples'], 'weighted_samples', source, at, 'omp_model_perf')
            grouped = defaultdict(list)
            for item in connection.execute('SELECT provider,account_key,limit_id,recorded_at,used_fraction,resets_at FROM usage_history ORDER BY recorded_at'):
                if provider_name(item['provider']) in PROVIDERS:
                    grouped[(provider_name(item['provider']), item['account_key'], item['limit_id'])].append(dict(item))
            for (provider, account, limit_id), samples in grouped.items():
                latest = samples[-1]
                at = timestamp(latest['recorded_at'])
                reset = number(latest['resets_at'])
                reset = reset / 1000 if reset and reset > 10**11 else reset
                latest_seconds = latest['recorded_at'] / 1000
                pool_id = hashlib.sha256(f'{provider}:{account}:{limit_id}'.encode()).hexdigest()[:20]
                used = number(latest['used_fraction'])
                burn = None
                # Same account, same limit, same reset period. Never bridge a reset.
                for previous in reversed(samples[:-1]):
                    prior_reset = number(previous['resets_at'])
                    prior_reset = prior_reset / 1000 if prior_reset and prior_reset > 10**11 else prior_reset
                    elapsed = latest_seconds - previous['recorded_at'] / 1000
                    prior_used = number(previous['used_fraction'])
                    if elapsed <= 0 or prior_used is None:
                        continue
                    if reset and prior_reset and abs(reset - prior_reset) <= 60 and used is not None and used >= prior_used:
                        burn = (used - prior_used) * 3600 / elapsed
                    break
                remaining = 1 - used if used is not None and 0 <= used <= 1 else None
                self.pools.append({'id': pool_id, 'provider': provider, 'limit_id': limit_id,
                    'scope': 'shared_account_pool_not_per_model',
                    'used_fraction': metric(used, 'fraction', source, at, 'account_observed'),
                    'remaining_fraction': metric(remaining, 'fraction', source, at, 'one_minus_used_fraction'),
                    'reset_at': metric(timestamp(reset), 'ISO8601', source, at, 'account_observed'),
                    'time_to_reset_seconds': metric(max(0, reset - self.now) if reset else None, 's', source, at, 'reset_minus_catalog_time'),
                    'quota_fraction_per_hour': metric(burn, 'fraction/h', source, at, 'last_same_reset_interval' if burn is not None else 'unknown'),
                    'spend_usd_per_hour': metric(unit='USD/h', reason='Subscription usage is not monetary spend')})
        except sqlite3.Error:
            self.issues.append({'source': source, 'issue': 'unsupported_omp_schema'})
        finally:
            connection.close()

    def rate_history(self, args):
        connection, source = self.connect(args.rate_db, 'rate_limit_catalog')
        if not connection:
            return
        try:
            grouped = defaultdict(list)
            for item in connection.execute('SELECT provider,model,ts,ttft_ms,timestamp_basis,granularity FROM requests WHERE ttft_ms IS NOT NULL AND ttft_ms >= 0'):
                if item['ts'] is not None and provider_name(item['provider']) in PROVIDERS:
                    grouped[(provider_name(item['provider']), item['model'])].append(dict(item))
            for (provider, model), items in grouped.items():
                row = self.row(provider, model)
                if row is None:
                    continue
                at = timestamp(max(item['ts'] for item in items))
                values = [item['ttft_ms'] for item in items]
                for name, fraction in (('ttft_p50_ms', .5), ('ttft_p95_ms', .95)):
                    row['performance'][name] = metric(quantile(values, fraction), 'ms', source, at, 'linear_quantile_of_recorded_ttft_field',
                        'Historical assistant-message samples; recorded message timestamp semantics are unverified; not wall-duration estimates')
                row['performance']['ttft_sample_count'] = metric(len(values), 'samples', source, at, 'recorded_ttft_field')
            grouped = defaultdict(list)
            for item in connection.execute('SELECT provider,model,ts,limit_id,dimension,limit_value,remaining,resets_at,provenance FROM quota_windows WHERE valid=1 AND model IS NOT NULL ORDER BY ts'):
                grouped[(provider_name(item['provider']), item['model'], item['limit_id'], item['dimension'])].append(dict(item))
            for (provider, model, limit_id, dimension), items in grouped.items():
                row = self.row(provider, model)
                if row is None:
                    continue
                item = items[-1]
                at = timestamp(item['ts'])
                row['quota_windows'].append({'limit_id': limit_id, 'dimension': dimension,
                    'scope': 'historical_model_tagged_window_account_sharing_unknown',
                    'limit': metric(item['limit_value'], dimension, source, at, item['provenance']),
                    'remaining': metric(item['remaining'], dimension, source, at, item['provenance']),
                    'reset_at': metric(timestamp(item['resets_at']), 'ISO8601', source, at, item['provenance'])})
        except sqlite3.Error:
            self.issues.append({'source': source, 'issue': 'unsupported_rate_catalog_schema'})
        finally:
            connection.close()

    def outcomes(self, args):
        connection, source = self.connect(args.outcomes_db, 'agent_outcomes_ledger')
        if connection:
            try:
                sql = '''SELECT provider,model,task_kind,outcome,COUNT(*) n,MAX(updated_at) at
                         FROM runs WHERE dup_of IS NULL GROUP BY provider,model,task_kind,outcome'''
                groups = defaultdict(list)
                for item in connection.execute(sql):
                    groups[(provider_name(item['provider']), item['model'], item['task_kind'])].append(dict(item))
                for (provider, model, task), items in groups.items():
                    row = self.row(provider, model)
                    if row is None:
                        continue
                    counts = {item['outcome']: item['n'] for item in items}
                    terminal = sum(counts.get(name, 0) for name in ('verified_success', 'claimed_success', 'failed', 'killed'))
                    at = timestamp(max(item['at'] for item in items))
                    row['task_classes'][task] = {'classification': 'ledger_keyword_heuristic',
                        'outcomes': metric(counts, 'runs', source, at, 'proxy_outcome_labels'),
                        'terminal_samples': metric(terminal, 'runs', source, at, 'proxy_outcome_labels'),
                        'proxy_verified_rate': metric(counts.get('verified_success', 0) / terminal if terminal else None,
                            'fraction', source, at, 'proxy_not_product_acceptance',
                            'Tests/PR/push evidence does not establish accepted product behavior'),
                        'p_accept': metric(unit='fraction', reason='No task acceptance verifier receipts in this ledger schema')}
            except sqlite3.Error:
                self.issues.append({'source': source, 'issue': 'unsupported_outcomes_schema'})
            finally:
                connection.close()
        source = self.source(args.observed, 'kerdoios_observations')
        groups = defaultdict(list)
        invalid = 0
        try:
            with Path(args.observed).expanduser().open() as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            invalid += 1
                            continue
                        if item.get('kind') == 'routing_quota':
                            continue
                        groups[(provider_name(item.get('provider')), item.get('model'), item.get('capability_id') or item.get('task_type') or 'unknown')].append(item)
                    except (ValueError, TypeError):
                        invalid += 1
        except OSError:
            self.issues.append({'source': source, 'issue': 'observations_unavailable'})
        for (provider, model, task), items in groups.items():
            row = self.row(provider, model)
            if row is None:
                continue
            at = self.sources[source]['snapshot_at']
            verified = [item for item in items if isinstance(item.get('verified_success'), bool)]
            row['task_classes'].setdefault(task, {'p_accept': metric(unit='fraction', reason='No acceptance receipts')})['kerdoios'] = {
                'samples': metric(len(items), 'observations', source, at, 'file_snapshot_not_event_time'),
                'verified_samples': metric(len(verified), 'observations', source, at, 'explicit_verified_success_flag'),
                'flag_success_rate': metric(sum(item['verified_success'] for item in verified) / len(verified) if verified else None,
                    'fraction', source, at, 'reported_flag_not_receipt_verified_acceptance'),
                'event_timestamp': None}
        if invalid:
            self.issues.append({'source': source, 'issue': 'invalid_jsonl_rows_skipped', 'count': metric(invalid, 'rows', source, self.at, 'parser_count')})

    def quota_observations(self, args):
        """Measure the recorded Go interval, never infer a complete daily cap."""
        path = Path(args.observed).expanduser()
        source = self.source(path, 'kerdoios_routing_quota')
        constraint = self.constraints.setdefault('opencode-go', {})
        constraint['pending_measurement'] = {
            'status': 'awaiting_first_actual_go_deepseek_429',
            'source': source,
            'scope_note': 'No complete account/day coverage is established by this log'}

        def event_time(value):
            if not isinstance(value, str):
                return None
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return parsed.timestamp() if parsed.tzinfo is not None else None
            except (ValueError, OverflowError):
                return None

        def headers_for(item):
            result = {}
            headers = item.get('quota_headers')
            if not isinstance(headers, dict):
                return result
            for key, value in headers.items():
                name = str(key).lower()
                if not (re.fullmatch(r'x-ratelimit-(?:limit|remaining|reset)-(?:requests|tokens)(?:-(?:minute|hour|day))?', name)
                        or name == 'retry-after'):
                    continue
                numeric = number(value)
                if numeric is not None and numeric >= 0:
                    result[name] = numeric
                elif event_time(value) is not None:
                    result[name] = timestamp(value)
                elif isinstance(value, str) and re.fullmatch(r'(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+', value):
                    result[name] = value
            return result

        events = []
        try:
            with path.open() as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if (item.get('kind') != 'routing_quota' or item.get('provider') != 'opencode-go'
                            or item.get('synthetic', False) is not False
                            or type(item.get('http_status')) is not int
                            or not 100 <= item['http_status'] <= 599
                            or not isinstance(item.get('model'), str)
                            or not isinstance(item.get('trace_id'), str) or not item['trace_id']):
                        continue
                    at = event_time(item.get('observed_at'))
                    if at is None or at > self.now:
                        continue
                    item = {**item, '_time': at, '_line': line_number, '_headers': headers_for(item)}
                    events.append(item)
        except OSError:
            return
        events.sort(key=lambda item: (item['_time'], item['_line']))
        first = next((item for item in events
                      if item['http_status'] == 429
                      and item['model'].lower().rsplit('/', 1)[-1].startswith('deepseek')), None)
        if first is None:
            return
        finding_source = source + ':' + str(first['_line'])
        self.sources[finding_source] = {
            **self.sources[source], 'line': first['_line'],
            'citation': f'{path}:{first["_line"]}', 'kind': 'observed_first_go_deepseek_429'}
        at = timestamp(first['_time'])
        group = first.get('quota_group')
        group = group if isinstance(group, str) and group else None
        recorded = {}
        if group is not None:
            for item in events:
                if item['_time'] > first['_time']:
                    break
                if item.get('quota_group') == group:
                    recorded.setdefault(item['trace_id'], item)
        items = list(recorded.values())

        def total_tokens(key):
            values = [item.get(key) for item in items]
            return (sum(values) if values and all(type(value) is int and value >= 0 for value in values)
                    else None)

        input_tokens, output_tokens = total_tokens('input_tokens'), total_tokens('output_tokens')
        reset_keys = ('x-ratelimit-reset-requests-day',)
        # Ambiguous reset-requests, relative reset seconds and retry-after never
        # become a day reset. Only explicitly day-scoped ISO timestamps qualify.
        reset = next((first['_headers'][key] for key in reset_keys
                      if key in first['_headers'] and event_time(first['_headers'][key]) is not None), None)
        constraint['pending_measurement']['status'] = 'first_429_observed_daily_cap_unproven'
        constraint['observed_first_429'] = {
            'provider': 'opencode-go', 'model': first['model'], 'trace_id': first['trace_id'],
            'source': finding_source, 'observed_at': at,
            'http_status': metric(429, 'HTTP_status', finding_source, at, 'actual_worker_response'),
            'quota_headers': metric(first['_headers'], 'allowlisted_header_values', finding_source, at, 'actual_response_headers'),
            'request_day_reset_at': metric(reset, 'ISO8601', finding_source, at,
                'explicit_requests_day_header' if reset else 'unknown'),
            'quota_group': group,
            'recorded_interval': {
                'started_at': timestamp(min(item['_time'] for item in items)) if items else None,
                'ended_at': at, 'scope': 'same_known_quota_group_recorded_attempts_only',
                'requests': metric(len(items) if items else None, 'attempts', finding_source, at, 'distinct_trace_ids_recorded_interval'),
                'input_tokens': metric(input_tokens, 'input_tokens', finding_source, at, 'recorded_interval_complete_token_fields_only'),
                'output_tokens': metric(output_tokens, 'output_tokens', finding_source, at, 'recorded_interval_complete_token_fields_only'),
                'tokens': metric(input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
                    'tokens', finding_source, at, 'recorded_interval_complete_token_fields_only'),
                'complete_daily_coverage': False},
            'daily_cap': metric(unit='requests/day', reason='A first 429 does not prove a daily cap or complete account/window coverage'),
            'scope_note': 'Observed recorded interval only, NOT a full daily cap; quota_group does not prove complete window coverage'}

    def findings(self, args):
        """Import narrowly scoped acceptance priors through the #211 reader."""
        # _read validates canonical bytes and the entire hash chain without
        # creating the lock file used by read_findings(). Never repair a ledger.
        repo_src = str(Path(__file__).resolve().parents[2] / 'src')
        if repo_src not in sys.path:
            sys.path.insert(0, repo_src)
        from z0int.policy.findings import FindingsError, _read, timestamp as finding_timestamp
        from z0int.policy.ingest import _product_verifier, wilson

        path = Path(args.findings).expanduser()
        source = self.source(path, 'canonical_findings')
        try:
            findings = _read(path)
        except FindingsError:
            self.sources[source]['status'] = 'rejected'
            self.issues.append({'source': source, 'issue': 'invalid_canonical_findings_chain_no_acceptance_imported'})
            return
        for finding in sorted(findings, key=lambda item: (finding_timestamp(item['ts']), item['id'])):
            subject, arm, measurement = finding['subject'], finding['arm'], finding['metric']
            if (finding.get('synthetic', False) or finding['evidence_class'] != 'measured'
                    or subject.get('family') != 'model.routing.acceptance'
                    or arm.get('kind') != 'frontier'
                    or measurement.get('name') != 'acceptance_rate'
                    or measurement.get('higher_is_better') is not True):
                continue
            observed = finding_timestamp(finding['ts']).timestamp()
            expires = finding_timestamp(finding['expires_at']).timestamp()
            if not observed <= self.now < expires:
                continue
            provider, model, task = arm.get('provider'), arm.get('model'), subject.get('task')
            if (provider not in PROVIDERS or not isinstance(model, str) or not model.strip()
                    or not isinstance(task, str) or not task.strip()
                    or task in ('all', 'any', 'unknown') or '*' in task or '*' in model):
                continue
            n, accepted = measurement['n'], measurement.get('accepted')
            value = measurement['value']
            if (type(accepted) is not int or not 0 <= accepted <= n
                    or not 0 <= value <= 1
                    or not math.isclose(value, accepted / n, rel_tol=1e-12, abs_tol=1e-12)
                    or measurement.get('ci_method') != 'wilson95'):
                continue
            low, high = wilson(accepted, n)
            if not all(math.isclose(measurement[key], expected, rel_tol=1e-7, abs_tol=1e-7)
                       for key, expected in (('ci_low', low), ('ci_high', high))):
                continue
            acceptance_kind = measurement.get('acceptance_kind')
            receipt = None
            if acceptance_kind == 'rubric_draft_quality':
                if (subject.get('job') != 'ShadowEvals' or finding.get('evidence_kind') != 'proxy'
                        or measurement.get('promotion_eligible') is not False):
                    continue
                basis = 'measured_rubric_acceptance_not_product_success'
            elif acceptance_kind == 'verified_product_outcome':
                if finding.get('evidence_kind') != 'product_outcome' or not finding.get('verifier_receipt'):
                    continue
                reference = dict(finding['verifier_receipt'])
                if reference.get('kind') == 'aodl_state_update':
                    reference['event_id'] = reference.get('receipt_id')
                receipt, _ = _product_verifier({
                    'verifier_receipt': reference, 'run_id': finding['source'].get('run_id')})
                # One bound product receipt proves one observed outcome only.
                if receipt is None or n != 1 or accepted != int(receipt['success']):
                    continue
                basis = 'verified_product_outcome_acceptance'
            else:
                continue
            row = self.row(provider, model)
            finding_source = source + ':' + finding['id']
            self.sources[finding_source] = {
                'path': str(path.resolve()), 'kind': 'canonical_acceptance_finding',
                'finding_id': finding['id'], 'captured_at': self.at,
                'snapshot_at': finding['ts'], 'expires_at': finding['expires_at'],
                'original_source_path': finding['source']['path'],
                'original_source_sha256': finding['source']['sha256'],
                'status': 'canonical_chain_validated'}
            prior = {**metric(value, 'fraction', finding_source, finding['ts'], basis),
                'finding_id': finding['id'], 'expires_at': finding['expires_at'],
                'n': n, 'accepted': accepted, 'ci_low': measurement['ci_low'],
                'ci_high': measurement['ci_high'], 'ci_method': measurement['ci_method'],
                'acceptance_kind': acceptance_kind, 'evidence_kind': finding.get('evidence_kind'),
                'promotion_eligible': measurement.get('promotion_eligible', False),
                'scope_note': finding['scope_note'], 'task_class': task}
            if receipt:
                prior['verifier_receipt'] = {key: receipt[key] for key in ('kind', 'path', 'sha256', 'receipt_id')}
            task_row = row['task_classes'].setdefault(task, {})
            task_row.setdefault('acceptance_findings', []).append(prior)
            task_row['p_accept'] = prior
            self.identity(row, finding_source, finding['ts'], 'exact_task_measured_acceptance_prior')

    def benchmarks(self, args):
        # This public claim applies only to the exact Haiku 4.5 model family.
        source = 'public:anthropic-haiku-4.5-launch'
        self.sources[source] = {'url': 'https://www.anthropic.com/news/claude-haiku-4-5',
            'kind': 'public_benchmark_claim', 'snapshot_at': '2026-10-24',
            'published_at': '2025-10-15T00:00:00+00:00', 'captured_at': self.at, 'status': 'retained_public_claim'}
        claim_at = '2025-10-15T00:00:00+00:00'
        for provider, model in (('anthropic', 'claude-haiku-4-5'), ('anthropic', 'claude-haiku-4-5-20251001'),
                                ('vercel', 'anthropic/claude-haiku-4.5'), ('nous', 'anthropic/claude-haiku-4.5'),
                                ('openrouter', 'anthropic/claude-haiku-4.5'), ('opencode-zen', 'claude-haiku-4-5')):
            row = self.row(provider, model)
            row['benchmarks'].append({'benchmark': 'SWE-bench Verified', 'task_class': 'coding.bugfix',
                'score': metric(.733, 'fraction', source, claim_at, 'vendor_reported_not_served_alias_replication'),
                'dataset_size': metric(500, 'problems', source, claim_at, 'vendor_reported'),
                'trials': metric(50, 'trials', source, claim_at, 'vendor_reported'),
                'protocol': 'bash and string-replace tools; 128K thinking budget; no test-time compute; prompt addition; default sampling',
                'floor': metric(reason='No owner-approved routing floor; benchmark score is not p_accept')})
        for path, kind, explanation in ((args.provider_benchmark, 'provider_wiring_check', 'Provider registration checks, not model capability or latency'),
                                        (args.capability_matrix, 'harness_config_inventory', 'MCP and memory inventory, not model capability evaluation')):
            source = self.source(path, kind)
            self.benchmark_sources.append({'source': source, 'disposition': 'not_a_model_benchmark', 'reason': explanation})
        for path in args.benchmark:
            data, source = self.load(path, 'external_benchmark')
            for item in (data or {}).get('rows', []):
                row = self.row(item.get('provider'), item.get('model'))
                score = number(item.get('score'))
                if row is None or score is None or not item.get('source') or not timestamp(item.get('observed_at')):
                    self.issues.append({'source': source, 'issue': 'benchmark_missing_identity_score_or_provenance'})
                    continue
                floor = number(item.get('floor'))
                if floor is not None and (not item.get('floor_source') or not timestamp(item.get('floor_observed_at'))):
                    self.issues.append({'source': source, 'issue': 'benchmark_floor_missing_provenance'})
                    continue
                entry = {'benchmark': item.get('benchmark'), 'task_class': item.get('task_class'),
                    'score': metric(score, item.get('unit'), source, timestamp(item['observed_at']), 'external_benchmark'),
                    'original_source': item['source'],
                    'floor': metric(floor, item.get('unit'), item.get('floor_source'), timestamp(item.get('floor_observed_at')), 'supplied_floor' if floor is not None else 'unknown')}
                row['benchmarks'].append(entry)

    def finish(self):
        # OpenCode is permitted only through evidenced free Go/Zen models.
        # A name containing \"free\" is not sufficient evidence.
        self.rows = {key: row for key, row in self.rows.items()
                     if row['provider'] not in ('opencode-zen', 'opencode-go')
                     or (row.get('free_status', {}).get('value') is True
                         and not any(row['pricing'][name]['value'] is not None
                                     and row['pricing'][name]['value'] > 0
                                     for name in ('input', 'output')))}
        for row in self.rows.values():
            row['quota_pool_ids'] = [pool['id'] for pool in self.pools if pool['provider'] == row['provider']]
            row['quota_pool_binding'] = 'provider-level candidates; model membership is not established by usage_history'
            row['documentation'] = [self.sources[ref]['citation'] for ref in self.doc_refs[row['provider']]]
            row['provider_constraints_ref'] = row['provider']
            if row['provider'] == 'cerebras':
                row['free_status'] = self.constraints['cerebras']['free_status']
            if row['provider'] == 'vercel':
                row['free_tier_eligible'] = metric(unit='boolean',
                    reason='Public model listing is not the free-credit eligibility subset')
            for rule in self.limit_rules:
                if rule['provider'] != row['provider']:
                    continue
                pattern = rule['model']
                matches = (pattern == row['model'] or pattern == '*'
                           or (pattern == '*:free' and (row['model'].endswith(':free') or row['model'] == 'openrouter/free')))
                if not matches:
                    continue
                citation = self.sources[rule['source']]['citation']
                if citation not in row['documentation']:
                    row['documentation'].append(citation)
                for dimension in ('rpm', 'rpd', 'tpm', 'tpd'):
                    row['quota_windows'].append({
                        'dimension': dimension, 'tier': rule['tier'], 'scope': rule['scope'],
                        'limit': metric(number(rule['limits'].get(dimension)), dimension,
                            rule['source'], rule['at'], 'retained_published_limit'),
                        'remaining': metric(unit=dimension, reason='Published limit is not account remaining'),
                        'reset_at': metric(unit='ISO8601', reason='No account-specific reset observed'),
                        'conditions': rule['limits'].get('note'),
                        'documentation': [self.sources[rule['source']]['citation']]})
        counts = Counter(row['provider'] for row in self.rows.values())
        self.sources['catalog'] = {'kind': 'generated_catalog', 'captured_at': self.at,
                                   'snapshot_at': self.at, 'status': 'generated'}
        return {'schema': 'z0.model_routing.catalog.v1', 'generated_at': self.at,
            'contract': {'optimizer': 'Kerdoios only; catalog contains no ranking or optimization',
                'metric': '{value,unit,source,observed_at,basis,reason?}; source resolves in sources',
                'unknown': 'value=null; never zero-fill missing observations',
                'opencode': 'Only Go/Zen models with sourced free_status=true; paid and unproven-free aliases are excluded',
                'subscription': 'List prices are API equivalents, not actual billed spend; account pools are shared',
                'timing': 'TTFT quantiles use recorded ttft fields only; wall duration is never TTFT',
                'acceptance': 'Only canonical measured exact-task rubric_draft_quality priors or receipt-verified product outcomes become p_accept; generic proxies and vendor scores never do',
                'freshness': 'Each metric retains observation time; generated_at is not source freshness',
                'privacy': 'No credentials, prompts, responses, emails or raw account IDs are retained'},
            'sources': self.sources, 'providers': [{'provider': p, 'model_count': metric(counts[p], 'models', 'catalog', self.at, 'generated_count'),
                'required_category': 'subscription' if p in ('openai-codex', 'anthropic') else 'speed' if p in ('cerebras', 'groq') else 'free_or_credit_candidate_not_entitlement_guarantee'} for p in PROVIDERS],
            'models': sorted(self.rows.values(), key=lambda row: (row['provider'], row['model'])),
            'provider_constraints': self.constraints,
            'quota_pools': self.pools, 'benchmark_source_assessment': self.benchmark_sources,
            'limitations': ['No monetary spend rate without actual billed spend intervals.',
                'Shared subscription usage cannot be attributed to one model.',
                'Rubric acceptance is draft-quality evidence, not product success; capability floors still require explicit threshold evidence.',
                'Historical observations may contain failed or retired models; catalog membership does not authorize dispatch.',
                'Local SQLite read transactions are consistent per file, not atomic across source files.'],
            'issues': self.issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('catalog.json'))
    parser.add_argument('--omp-db', type=Path, default=Path.home() / '.omp/agent/agent.db')
    parser.add_argument('--omp-models-db', type=Path, default=Path.home() / '.omp/agent/models.db')
    parser.add_argument('--provider-cache', type=Path, default=HERMES / 'provider_models_cache.json')
    parser.add_argument('--inventory', type=Path, default=Path.home() / '.cache/kerdoios/inventory.json')
    parser.add_argument('--observed', type=Path, default=Path.home() / '.hermes/cache/kerdoios/observed.jsonl')
    parser.add_argument('--nous-metadata', type=Path, default=HERMES / 'cache/endpoint_model_metadata.json')
    parser.add_argument('--outcomes-db', type=Path, default=OPS / 'agent-outcomes/ledger.sqlite')
    parser.add_argument('--rate-db', type=Path, default=OPS / 'rate-limits/catalog.sqlite')
    parser.add_argument('--provider-benchmark', type=Path, default=Path('/workspace/hermes-home/profiles/jev-provider-bench/cache/jev_provider_verify.json'))
    parser.add_argument('--capability-matrix', type=Path, default=OPS / 'capability-matrix/20261022.md')
    parser.add_argument('--free-tier-docs', type=Path, default=Path('/workspace/hermes-home/profiles/clean/skills/research/offline-rate-limit-catalog/references/free-tier-providers.md'))
    parser.add_argument('--provider-guidance', type=Path, default=HERMES / 'skills/autonomous-ai-agents/hermes-agent/references/providers-and-models.md')
    parser.add_argument('--free-tier-catalog', type=Path, default=OPS / 'free-tier-research/20261019/catalog.json')
    parser.add_argument('--findings', type=Path, default=Path.home() / '.z0int/findings/findings.jsonl')
    parser.add_argument('--benchmark', type=Path, action='append', default=[])
    parser.add_argument('--public-models', action='append', default=[], metavar='PROVIDER=JSON_PATH')
    parser.add_argument('--refresh-public', action='store_true', help='Unauthenticated metadata GETs only; never inference')
    args = parser.parse_args()
    catalog = Catalog(datetime.now(timezone.utc).timestamp())
    catalog.seed_documentation(args)
    catalog.model_metadata(args)
    catalog.public_metadata(args)
    catalog.omp(args)
    catalog.rate_history(args)
    catalog.outcomes(args)
    catalog.quota_observations(args)
    catalog.benchmarks(args)
    catalog.findings(args)
    payload = catalog.finish()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'schema': payload['schema'], 'output': str(args.output), 'generated_at': payload['generated_at'],
                      'models': len(payload['models']), 'providers': {item['provider']: item['model_count']['value'] for item in payload['providers']},
                      'quota_pools': len(payload['quota_pools']), 'issues': len(payload['issues'])}))


if __name__ == '__main__':
    main()
