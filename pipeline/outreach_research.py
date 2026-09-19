"""Private, hash-bound research review. No mail or public dataset writes.

Mapping is prepared by the existing offline Intake validator. This module exposes
that mapping for human review, keeping source facts and publication separate.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import urlsplit

from outreach_evidence import evidence


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write((json.dumps(value, indent=2, ensure_ascii=False)+'\n').encode())
        name = f.name
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read(path, fallback):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else fallback


def load_state(run, docs):
    state = read(Path(run)/'research/state.json', {'version': 1, 'packets': {}, 'events': []})
    seed = Path(__file__).with_name('lancaster_research.json')
    if seed.exists():
        packet = read(seed, {})
        try:
            verify_packet(packet, docs)
        except ValueError:
            pass  # Never expose another workspace's evidence.
        else:
            state['packets'].setdefault(digest(packet), packet)
    return state


def inventory(run):
    """Read only matched messages in the selected workspace; never fetch Gmail."""
    with closing(sqlite3.connect((Path(run)/'live.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        ids = [r[0] for r in db.execute('SELECT id FROM incoming WHERE rid IN (SELECT id FROM requests)')]
    docs = []
    for mid in ids:
        info, _, _ = evidence(run, mid)
        for a in info['attachments']:
            docs.append({**a, 'mid': mid, 'rid': info['rid'], 'message_sha256': info['raw_sha256']})
    return docs


def verify_packet(packet, docs):
    if packet.get('kind') not in {'contract_review', 'annual_review'} or not packet.get('title'):
        raise ValueError('Expected a validated private review packet.')
    if not packet.get('sources') or not packet.get('facts') or not isinstance(packet.get('blockers'), list):
        raise ValueError('Sources, facts and publication blockers must be explicit.')
    for source in packet['sources']:
        if not any(d['available'] and all(d.get(k) == source.get(k) for k in ('mid', 'rid', 'sha256', 'message_sha256')) for d in docs):
            raise ValueError('Source is missing, changed or belongs to another workspace.')
        if not re.fullmatch('[a-f0-9]{64}', source.get('extraction_sha256', '')):
            raise ValueError('Every source needs an extraction version hash.')
    for fact in packet['facts']:
        if not fact.get('evidence'):
            raise ValueError('Every fact needs page evidence.')
        for proof in fact['evidence']:
            source = next((s for s in packet['sources'] if s['sha256'] == proof.get('sha256')), None)
            if not source or type(proof.get('page')) is not int or not 1 <= proof['page'] <= source['pages'] or not proof.get('locator'):
                raise ValueError('Invalid source page or field locator.')
    if packet['kind'] == 'annual_review':
        ticket = packet.get('intake_ticket', {})
        if ticket.get('kind') != 'private_outreach_handoff' or len(ticket.get('rows', [])) != 1:
            raise ValueError('Import one validated annual observation per review packet.')
        if not packet.get('intake_candidate_sha256') or not ticket.get('base_active_sha256'):
            raise ValueError('Intake candidate and active baseline hashes are required.')
    elif packet.get('intake_ticket'):
        raise ValueError('Contract evidence cannot contain annual rows.')


def view(run):
    folder = Path(run)/'research'
    docs = inventory(run)
    state = load_state(run, docs)
    candidates = []
    for key, packet in state['packets'].items():
        events = [e for e in state['events'] if e['candidate'] == key]
        facts = next((e for e in reversed(events) if e['gate'] == 'facts'), None)
        publication = next((e for e in reversed(events) if e['gate'] == 'publication'), None)
        # New fact reviews invalidate the earlier publication decision.
        if publication and (not facts or publication['fact_review'] != facts['id']):
            publication = None
        try:
            verify_packet(packet, docs)
            integrity = 'verified'
        except ValueError:
            integrity = 'source changed or unavailable; review blocked'
        receipts=[r for r in state.get('receipts',[]) if any(link['workspace_candidate']==key for link in r['review_links'])]
        candidates.append(dict(id=key, packet=packet, history=events, facts=facts, publication=publication, integrity=integrity, receipts=receipts))
    return dict(documents=docs, candidates=candidates, revision=digest(state),
                publication='Private review only. Public snapshot acceptance and deployment require the separate release gate.')


def mutate(run, data, actor):
    """Caller holds the workspace service lock and has checked owner + CSRF."""
    folder = Path(run)/'research'
    docs = inventory(run)
    state = load_state(run, docs)
    if data.get('revision') != digest(state):
        raise ValueError('Review changed. Reload before recording a decision.')
    command = data.get('command')
    if command == 'research_receipt':
        receipt=data.get('receipt',{})
        if receipt.get('kind')!='publication_deployment_receipt' or not re.fullmatch('[a-f0-9]{64}',receipt.get('snapshot_id','')) or not receipt.get('deployment_id') or not receipt.get('build_files'):
            raise ValueError('Complete deployment receipt required.')
        parsed=urlsplit(receipt.get('url',''))
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Deployment URL must be HTTPS without credentials.')
        if not receipt.get('review_links'):
            raise ValueError('Deployment must link the exact private publication review.')
        for link in receipt['review_links']:
            key=link['workspace_candidate'];current=next((c for c in view(run)['candidates'] if c['id']==key),None)
            if not current or current['integrity']!='verified' or not current['publication'] or current['publication']['decision']!='eligible' or current['publication']['id']!=link['publication_review_id'] or link['release_id'] not in receipt.get('publication_releases',[]):
                raise ValueError('Deployment receipt does not match current publication eligibility.')
        receipt={**receipt,'recorded_in_workspace_by':actor}
        receipts=state.setdefault('receipts',[])
        if receipt not in receipts:receipts.append(receipt)
    elif command == 'research_import':
        packet = data.get('packet', {})
        verify_packet(packet, docs)
        key = digest(packet)
        state['packets'].setdefault(key, packet)
    else:
        key = data.get('candidate')
        packet = state['packets'].get(key)
        if not packet:
            raise ValueError('Candidate not found in this workspace.')
        verify_packet(packet, docs)
        note = data.get('note')
        if not isinstance(note, str) or not 8 <= len(note.strip()) <= 4000:
            raise ValueError('Explain the evidence behind this decision (8–4000 characters).')
        event = dict(candidate=key, actor=actor, at=datetime.now(timezone.utc).isoformat(),
                     reason=note.strip(), decision=data.get('decision'))
        if command == 'research_decide':
            if event['decision'] not in {'accept', 'hold', 'exclude', 'supersede'}:
                raise ValueError('Choose accept, hold, exclude or supersede.')
            targets = data.get('supersedes', [])
            conflicts = packet.get('intake_ticket', {}).get('conflicts', [])
            known = {c['existing_id'] for c in conflicts}
            if event['decision'] == 'supersede' and (not targets or not set(targets) <= known):
                raise ValueError('Supersession requires identified predecessor rows from the candidate diff.')
            if event['decision'] != 'supersede' and targets:
                raise ValueError('Only supersede may select predecessor rows.')
            event.update(gate='facts', supersedes=targets, overlap_resolutions=data.get('overlap_resolutions', {}))
            if not isinstance(event['overlap_resolutions'], dict):
                raise ValueError('Overlap decisions must be an object keyed by predecessor row.')
            if event['decision'] in {'accept', 'supersede'} and any(not str(event['overlap_resolutions'].get(k, '')).strip() for k in known):
                raise ValueError('Resolve every possible overlap with an evidence-based reason.')
        elif command == 'research_publication':
            facts = next((e for e in reversed(state['events']) if e['candidate'] == key and e['gate'] == 'facts'), None)
            if event['decision'] not in {'eligible', 'hold'}:
                raise ValueError('Choose eligible or hold for publication.')
            if event['decision'] == 'eligible' and (packet['kind'] != 'annual_review' or packet['blockers'] or not facts or facts['decision'] not in {'accept', 'supersede'}):
                raise ValueError('Accepted annual facts with no unresolved publication blockers are required.')
            event.update(gate='publication', fact_review=facts['id'] if facts else None)
        else:
            raise ValueError('Unknown research action.')
        event['id'] = digest({**event, 'sequence': len(state['events'])})
        state['events'].append(event)
    atomic(folder/'state.json', state)
    return {'ok': True, 'candidate': key}


def export_review(run, key):
    result = view(run)
    candidate = next((c for c in result['candidates'] if c['id'] == key), None)
    if not candidate or candidate['integrity'] != 'verified':
        raise ValueError('Verified candidate required.')
    if not candidate['publication'] or candidate['publication']['decision'] != 'eligible':
        raise ValueError('Publication eligibility review is required before handoff.')
    return {'kind': 'workspace_review_export', **candidate, 'public_snapshot_changed': False}
