"""Independent, read-only official-source monitoring. Never imports mail or publication code."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urljoin, unquote, urldefrag
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
MAX_BYTES = 20*1024*1024
CACHE_LIMIT = 24*1024*1024
HOSTS = {'www.michigan.gov', 'www.pa.gov'}
ZONE = ZoneInfo('America/New_York')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def official(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443) or len(url)>2000:
        raise ValueError('Only registered official HTTPS hosts are permitted')
    return urldefrag(url)[0]


def next_check(now, cadence='seasonal'):
    """Calendar schedule at 09:00 Eastern, DST-aware; never an elapsed 24-hour guess."""
    local = datetime.fromtimestamp(now, ZONE)
    for n in range(9):
        date = local.date()+timedelta(days=n)
        candidate = datetime(date.year,date.month,date.day,9,tzinfo=ZONE)
        daily = cadence == 'daily' or cadence == 'seasonal' and 6 <= date.month <= 8
        if candidate.timestamp()>now and (daily or date.weekday()==0):
            return candidate.timestamp()
    raise ValueError('Unknown calendar schedule')


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href: self.hrefs.append(href)


def discover(body, url, state):
    parser=Links(); parser.feed(body.decode('utf-8', errors='replace'))
    found=set()
    for href in parser.hrefs:
        target=urljoin(url,href)
        try: target=official(target)
        except ValueError: continue
        path=unquote(urlsplit(target).path).lower()
        same=urlsplit(target).hostname==urlsplit(url).hostname
        relevant=('/procurement/contracts/' in path and '/supporting-documents/' not in path and 'w-9' not in path if state=='MI' else 'costars/' in path and ('sodium' in path or 'road salt' in path))
        if same and path.endswith('.pdf') and relevant: found.add(target)
    if not found: raise ValueError('No relevant PDF links found; listing coverage needs review')
    if len(found)>30: raise ValueError('Listing exceeds 30-document discovery limit')
    return sorted(found)


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def retrieve(source, output):
    """Runs in a killable child. No cookies, identity, Gmail tokens or browser bypass."""
    url=official(source['url'])
    opener=urllib.request.build_opener(Redirect())
    req=urllib.request.Request(url,headers={'User-Agent':'RoadSaltContractResearch/1.0','Accept':'application/pdf,text/html','Accept-Encoding':'identity'})
    with opener.open(req,timeout=12) as response:
        official(response.url)
        if response.status!=200: raise ValueError('Unexpected HTTP response')
        limit=MAX_BYTES if source['kind']=='pdf' else 2*1024*1024
        if int(response.headers.get('Content-Length','0'))>limit: raise ValueError('Source exceeds download limit')
        body=response.read(limit+1)
        if len(body)>limit: raise ValueError('Source exceeds download limit')
        result=dict(sha256=sha(body),bytes=len(body),final_url=response.url,http_status=200,
                    content_type=response.headers.get('Content-Type','')[:100],
                    etag=response.headers.get('ETag','')[:200],last_modified=response.headers.get('Last-Modified','')[:100])
    if source['kind']=='pdf':
        if not body.startswith(b'%PDF-'): raise ValueError('Not a PDF; possible error or login page')
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(body),strict=True)
        if reader.is_encrypted: raise ValueError('Encrypted PDF needs manual retrieval')
        pages=len(reader.pages)
        if not 0<pages<=1000: raise ValueError('Invalid or excessive PDF page count')
        result['pages']=pages
    else:
        if 'html' not in result['content_type'].lower(): raise ValueError('Listing response is not HTML')
        result['links']=discover(body,result['final_url'],source['state'])
    Path(output).write_bytes(body)
    return result


def fetch(source, output):
    try:
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'fetch',json.dumps(source),str(output)],
                            capture_output=True,text=True,timeout=40,check=False,
                            env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','TEMP','TMP','SSL_CERT_FILE','SSL_CERT_DIR'}})
        if proc.returncode or not proc.stdout: raise ValueError('Source reader failed; inspect official page manually')
        result=json.loads(proc.stdout)
        if result.get('error'): raise ValueError(result['error'])
        return result
    except subprocess.TimeoutExpired:
        raise ValueError('Source retrieval or PDF validation exceeded 40 seconds') from None


class Monitor:
    def __init__(self, folder, catalog=None, hold=lambda:False):
        self.folder=Path(folder)/'source-monitor'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.hold=hold
        catalog=catalog or json.loads((HERE/'source_monitor_catalog.json').read_text())
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS monitor_config(id INTEGER PRIMARY KEY,enabled INTEGER,cadence TEXT,heartbeat REAL,last_manual REAL);
            INSERT OR IGNORE INTO monitor_config VALUES(1,0,'seasonal',NULL,NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,url TEXT UNIQUE,kind TEXT,state TEXT,baseline TEXT,
              last_attempt REAL,last_success REAL,next_run REAL,status TEXT,error TEXT,observed TEXT,links TEXT,claim TEXT,claimed REAL,requested INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,source TEXT,at REAL,status TEXT,receipt TEXT);
            CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,source TEXT,kind TEXT,hash TEXT,created REAL,status TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,source TEXT,error TEXT,first_seen REAL,last_seen REAL,occurrences INTEGER,resolved REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL,actor TEXT,action TEXT,item TEXT,note TEXT);
            ''')
            for d in catalog['sources']:
                official(d['url'])
                db.execute('INSERT OR IGNORE INTO sources(id,url,kind,state,baseline,next_run,status) VALUES(?,?,?,?,?,0,?)',
                           (d['id'],d['url'],d['kind'],d['state'],d.get('sha256'), 'never_checked'))

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.folder/'monitor.sqlite3',timeout=5)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def view(self, now=None):
        now=time.time() if now is None else now
        with self.db() as db:
            config=dict(db.execute('SELECT * FROM monitor_config').fetchone())
            result=dict(config=config,sources=[dict(r) for r in db.execute('SELECT * FROM sources ORDER BY state,kind,id')],
                        queue=[dict(r) for r in db.execute('SELECT * FROM queue ORDER BY created DESC')],
                        incidents=[dict(r) for r in db.execute('SELECT * FROM incidents ORDER BY last_seen DESC')],
                        events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')],
                        checks=[dict(r) for r in db.execute('SELECT * FROM checks ORDER BY id DESC LIMIT 100')])
        for s in result['sources']:
            s.pop('claim');s.pop('claimed');s['links']=json.loads(s['links'] or '[]')
        for q in result['queue']: q['detail']=json.loads(q['detail'])
        for c in result['checks']: c['receipt']=json.loads(c['receipt'])
        result['recovery_hold']=self.hold()
        result['worker_stale']=bool(config['enabled'] and (not config['heartbeat'] or now-config['heartbeat']>180))
        result['publication']='Discovery only. Downloaded documents require extraction, source-fact review and separate publication acceptance. No dashboard data changes here.'
        return result

    def control(self, data, actor, now=None):
        now=time.time() if now is None else now
        command=data['command']
        if self.hold() and command!='monitor_pause': raise ValueError('Recovery quarantine: source monitoring is disabled')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cfg=db.execute('SELECT * FROM monitor_config').fetchone()
            if command=='monitor_enable':
                cadence=data.get('cadence','seasonal')
                if cadence not in ('seasonal','daily','weekly'): raise ValueError('Invalid cadence')
                db.execute('UPDATE monitor_config SET enabled=1,cadence=?',(cadence,))
                db.execute('UPDATE sources SET next_run=? WHERE last_attempt IS NOT NULL',(next_check(now,cadence),))
            elif command=='monitor_pause':
                db.execute('UPDATE monitor_config SET enabled=0')
                db.execute('UPDATE sources SET requested=0')
            elif command=='monitor_check':
                if cfg['last_manual'] and now-cfg['last_manual']<900: raise ValueError('Check already requested; wait 15 minutes before requesting another')
                db.execute('UPDATE monitor_config SET last_manual=?',(now,))
                db.execute('UPDATE sources SET requested=1 WHERE claim IS NULL')
            elif command=='monitor_review':
                decision=data.get('decision');note=data.get('note','').strip();item=data.get('item')
                if decision not in ('needs_review','held','excluded','handed_off') or not 8<=len(note)<=2000:
                    raise ValueError('A review state and evidence note (8–2000 characters) are required')
                row=db.execute('SELECT status FROM queue WHERE id=?',(item,)).fetchone()
                if not row or row['status']!=data.get('expected'): raise ValueError('Queue item changed; reload before reviewing')
                db.execute('UPDATE queue SET status=? WHERE id=?',(decision,item))
            else: raise ValueError('Unknown monitor command')
            db.execute('INSERT INTO events(at,actor,action,item,note) VALUES(?,?,?,?,?)',
                       (now,actor,command,data.get('item',''),(data.get('decision','')+' '+data.get('note',data.get('cadence',''))).strip()))
        return {'ok':True}

    def enqueue(self, db, source, kind, digest, detail, now):
        identity=source['state']+'|pdf' if source['kind']=='pdf' else source['id']+'|'+kind
        key=sha((identity+'|'+digest).encode())
        db.execute('INSERT OR IGNORE INTO queue VALUES(?,?,?,?,?,?,?)',
                   (key,source['id'],kind,digest,now,'needs_review',json.dumps(detail)))

    def fail(self, db, source, error, now):
        key=sha((source['id']+'|'+error).encode())
        db.execute('INSERT INTO incidents VALUES(?,?,?,?,?,1,NULL) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,occurrences=incidents.occurrences+1,resolved=NULL',
                   (key,source['id'],error,now,now))
        db.execute("UPDATE sources SET status='failed',error=?,claim=NULL,claimed=NULL WHERE id=?",(error,source['id']))
        db.execute('INSERT INTO checks(source,at,status,receipt) VALUES(?,?,?,?)',(source['id'],now,'failed',json.dumps({'error':error})))

    def tick(self, fetcher=fetch, now=None):
        now=time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE monitor_config SET heartbeat=?',(now,))
            cfg=db.execute('SELECT * FROM monitor_config').fetchone()
            for row in db.execute('SELECT * FROM sources WHERE claim IS NOT NULL AND claimed<?',(now-120,)).fetchall():
                self.fail(db,dict(row),'Interrupted source check; retained prior evidence',now)
            if self.hold(): return 'recovery_hold'
            row=db.execute('SELECT * FROM sources WHERE claim IS NULL AND (requested=1 OR (?=1 AND next_run<=?)) ORDER BY requested DESC,next_run,id LIMIT 1',(cfg['enabled'],now)).fetchone()
            if not row: return 'idle'
            source=dict(row);claim=os.urandom(16).hex()
            db.execute("UPDATE sources SET claim=?,claimed=?,last_attempt=?,next_run=?,requested=0,status='checking' WHERE id=?",
                       (claim,now,now,next_check(now,cfg['cadence']),source['id']))
        try:
            with tempfile.TemporaryDirectory(prefix='road-salt-source-') as tmp:
                output=Path(tmp)/'download'
                result=fetcher(source,output)
                blob=output.read_bytes()
                if len(blob)>MAX_BYTES or sha(blob)!=result['sha256']: raise ValueError('Retrieval hash or size mismatch')
                official(result['final_url'])
                if source['kind']=='pdf' and (not blob.startswith(b'%PDF-') or not result.get('pages')): raise ValueError('PDF validation missing')
                with self.db() as db:
                    reviewed=db.execute('SELECT id FROM sources WHERE state=? AND baseline=?',(source['state'],result['sha256'])).fetchone()
                if reviewed: result['matches_reviewed_source']=reviewed['id']
                # Store only changed PDFs and listing receipts. Never execute/render HTML.
                store=(json.loads(source['links'] or '[]')!=result.get('links') if source['kind']=='listing' else not reviewed)
                if store:
                    objects=self.folder/'objects';objects.mkdir(exist_ok=True)
                    target=objects/result['sha256']
                    if not target.exists():
                        if sum(p.stat().st_size for p in objects.iterdir())+len(blob)>CACHE_LIMIT: raise ValueError('Evidence cache limit reached; owner must archive evidence before further retrieval')
                        pending=objects/(result['sha256']+'.pending')
                        pending.write_bytes(blob)
                        os.replace(pending,target)
                    elif sha(target.read_bytes())!=result['sha256']: raise ValueError('Stored source evidence hash mismatch')
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('SELECT claim FROM sources WHERE id=?',(source['id'],)).fetchone()[0]!=claim: return 'superseded'
                    status='unchanged'
                    if source['kind']=='listing':
                        links=result['links'];prior=json.loads(source['links'] or '[]')
                        added=sorted(set(links)-set(prior));removed=sorted(set(prior)-set(links))
                        if added or removed:
                            status='listing_changed' if source['links'] else 'listing_baselined'
                            self.enqueue(db,source,status,sha(json.dumps(links).encode()),dict(added=added,removed=removed,listing_sha256=result['sha256']),now)
                        for link in links:
                            official(link)
                            if not db.execute('SELECT 1 FROM sources WHERE url=?',(link,)).fetchone():
                                if db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]>=60: raise ValueError('Discovery limit reached; owner review required')
                                paused=db.execute("SELECT 1 FROM events WHERE action='monitor_pause' AND at>=?",(now,)).fetchone()
                                db.execute('INSERT INTO sources(id,url,kind,state,next_run,status,requested) VALUES(?,?,?,?,0,?,?)',
                                           ('discovered-'+sha(link.encode())[:20],link,'pdf',source['state'],'never_checked',0 if paused else 1))
                        db.execute('UPDATE sources SET links=? WHERE id=?',(json.dumps(links),source['id']))
                    elif reviewed:
                        status='unchanged' if result['sha256']==source['baseline'] else 'matches_reviewed_bytes'
                    elif result['sha256']!=source['baseline']:
                        status='changed_requires_review' if source['baseline'] else 'new_requires_review'
                        self.enqueue(db,source,status,result['sha256'],dict(accepted_sha256=source['baseline'],retrieval=result,extraction='Not extracted; PDF structure check only'),now)
                    db.execute('UPDATE sources SET last_success=?,status=?,error=NULL,observed=?,claim=NULL,claimed=NULL WHERE id=?',
                               (now,status,result['sha256'],source['id']))
                    db.execute('UPDATE incidents SET resolved=? WHERE source=? AND resolved IS NULL',(now,source['id']))
                    db.execute('INSERT INTO checks(source,at,status,receipt) VALUES(?,?,?,?)',(source['id'],now,status,json.dumps(result)))
            return status
        except Exception as exc:
            # Errors deliberately exclude arbitrary remote bodies and local paths.
            error=str(exc)[:250] if isinstance(exc,ValueError) else type(exc).__name__+': source retrieval/storage failed'
            with self.db() as db: self.fail(db,source,error,now)
            return 'failed'

    def document(self, item):
        with self.db() as db:
            row=db.execute('SELECT * FROM queue WHERE id=?',(item,)).fetchone()
        if not row or row['kind'] not in ('changed_requires_review','new_requires_review'): raise ValueError('No downloadable PDF for this queue item')
        path=self.folder/'objects'/row['hash']
        data=path.read_bytes()
        if sha(data)!=row['hash']: raise ValueError('Evidence hash mismatch')
        return data


if __name__=='__main__':
    if sys.argv[1]=='fetch':
        try:
            # Linux deployment bounds parser address space as well as parent wall time.
            if sys.platform=='linux':
                import resource
                resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
            result=retrieve(json.loads(sys.argv[2]),sys.argv[3])
        except urllib.error.HTTPError as error: result={'error':'HTTP '+str(error.code)+'; source unavailable, prior evidence retained'}
        except ValueError as error: result={'error':str(error)[:250]}
        except Exception as error: result={'error':type(error).__name__+': retrieval or PDF validation failed'}
        print(json.dumps(result))
