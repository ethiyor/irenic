"""Read-only workflow projection. Operational states are never rewritten here."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import time
from outreach_evidence import evidence

JOB_REASONS = {'pending':'Awaiting exact-message approval.', 'rejected':'Rejected by an approver; not automatically recreated.',
 'cancelled':'No longer eligible after a reply or campaign change.', 'uncertain':'Provider outcome unresolved. Do not resend; owner must reconcile.',
 'sending':'Send attempt in progress or interrupted. Do not retry until reconciled.', 'sent':'Provider accepted; not proof of delivery or reading.'}

def workflow(service, state):
    counts=Counter(r['state'] for r in state['requests'])
    jobs=state['jobs']
    summary=dict(waiting=counts['waiting'],held=counts['held'],suppressed=counts['suppressed'],
        disabled=sum(not r['enabled'] for r in state['requests']),classification=sum(m['kind']=='unknown' for m in state['incoming']),
        initial_sent=sum(j['state']=='sent' and j['kind']=='request' and j['stage']==0 for j in jobs),
        reminders_sent=sum(j['state']=='sent' and j['kind']=='request' and j['stage']>0 for j in jobs),
        forwards_sent=sum(j['state']=='sent' and j['kind']=='forward' for j in jobs))
    docs=[]
    for m in state['incoming']:
        if not m['rid']: continue
        try:
            info,_,_=evidence(service.run,m['id'])
            for d in info['attachments']:
                docs.append(dict(message=m['id'],rid=m['rid'],name=d['name'],state='excluded' if d['decision']=='exclude' else 'held' if d['decision']=='include' else 'needs review',reason=d['review']))
        except (ValueError,LookupError,OSError,sqlite3.Error,RecursionError):
            docs.append(dict(message=m['id'],rid=m['rid'],name='Message evidence',state='needs review',reason='Evidence unavailable; owner review needed.'))
    summary['documents_open']=sum(d['state'] in {'held','needs review'} for d in docs)
    source=Path(__file__).resolve().parents[1]/'data/outreach/milestone-9/contacts.json'
    contacts=json.loads(source.read_text())['contacts'] if source.exists() else []
    with closing(sqlite3.connect((Path(service.run)/'live.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        audit=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY seq')]
    timelines=[]
    for r in state['requests']:
        mids=[m['id'] for m in state['incoming'] if m['rid']==r['id']]
        related=[j for j in jobs if j['rid']==r['id']]
        keys=[r['id'],*mids,*[j['key'] for j in related]]
        events=[a for a in audit if any(k in a['detail'] for k in keys)]
        contact=next((c for c in contacts if c['contact_id']==r['id'] and c['email'].lower()==r['to'].lower()),None)
        timelines.append(dict(rid=r['id'],organization=r['organization'],state=r['state'],enabled=r['enabled'],
            due=r['due'],provenance=dict(url=contact['source_url'],verified=contact['verified_on']) if contact else None,
            events=events,jobs=[dict(j,reason=JOB_REASONS.get(j['state'],'Recorded state; owner review required.')) for j in related],
            replies=[m['id'] for m in state['incoming'] if m['rid']==r['id']],documents=[d for d in docs if d['rid']==r['id']],
            publication='No publication link recorded in this campaign ledger; evidence is not automatically published.'))
    return dict(as_of=time.time(),counts=summary,documents=docs,timelines=timelines,
                jobs=[dict(j,reason=JOB_REASONS.get(j['state'],'Recorded state; owner review required.')) for j in jobs])
