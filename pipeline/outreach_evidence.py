"""Read-only, bounded MIME evidence. Never fetch resources or expand archives."""
from contextlib import closing
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re
import sqlite3

MAX_MESSAGE = 32 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
MAX_PARTS = 100

class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.out = []
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','template','iframe','object','svg','form'}:
            self.hidden += 1
        if not self.hidden and tag in {'p','div','br','li','tr'}:
            self.out.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script','style','template','iframe','object','svg','form'}:
            self.hidden = max(0, self.hidden-1)
    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)

def safe_name(name):
    name = re.sub(r'[^A-Za-z0-9._ -]', '_', str(name or 'attachment'))
    return name.strip(' .')[:140] or 'attachment'

def evidence(folder, mid):
    path = Path(folder)/'live.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT id,rid,kind,length(raw) AS size FROM incoming WHERE id=? AND rid IN (SELECT id FROM requests)', (mid,)).fetchone()
        if not row:
            raise LookupError('Matched reply not found in this workspace.')
        if row['size'] > MAX_MESSAGE:
            raise ValueError('Message exceeds the 32 MiB review limit; ask the owner to review the original.')
        raw = db.execute('SELECT raw FROM incoming WHERE id=?', (mid,)).fetchone()[0]
        history = [dict(r) for r in db.execute("SELECT at,event,detail FROM audit WHERE event LIKE 'classified_%' ORDER BY seq") if r['detail'].startswith(mid+': ')]
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    parts = list(msg.walk())
    if len(parts) > MAX_PARTS:
        raise ValueError('Message has too many MIME parts to review safely.')
    body = msg.get_body(preferencelist=('plain','html'))
    text = 'No readable body. Download the original message for specialist review.'
    if body:
        try:
            text = body.get_content()
            if not isinstance(text,str):
                raise ValueError()
            if body.get_content_type() == 'text/html':
                parser = TextOnly(); parser.feed(text[:500000]); text = ''.join(parser.out)
            text = text[:50000] or 'No readable text extracted.'
        except (ValueError, LookupError, UnicodeError):
            text = 'Body could not be decoded. Original message remains available.'
    review_file = Path(__file__).with_name('evidence_reviews.json')
    reviews = json.loads(review_file.read_text()) if review_file.exists() else {}
    manifest, payloads = [], {}
    for index, part in enumerate(parts):
        if part.is_multipart() or part is body:
            continue
        if not (part.get_filename() or part.get_content_disposition() == 'attachment'):
            continue
        data = part.get_payload(decode=True) or b''
        digest = hashlib.sha256(data).hexdigest()
        fid = hashlib.sha256((mid+':'+str(index)+':'+digest).encode()).hexdigest()
        detected = 'application/pdf' if data.startswith(b'%PDF-') else 'unsupported'
        state = ('Too large: 12 MiB limit' if len(data)>MAX_FILE else
                 'Corrupt or empty PDF' if detected=='application/pdf' and b'%%EOF' not in data[-4096:] else
                 'Quarantined: unsupported type' if detected=='unsupported' else 'Download available')
        available = state == 'Download available'
        review = reviews.get(digest, {})
        manifest.append(dict(id=fid,name=safe_name(part.get_filename()),bytes=len(data),sha256=digest,
            detected_type=detected,declared_type=part.get_content_type(),state=state,available=available,
            review=review.get('reason','Not reviewed; not accepted for publication.'),decision=review.get('decision','needs review')))
        if available:
            payloads[fid] = (data,safe_name(part.get_filename()))
    result = dict(id=mid,rid=row['rid'],classification=row['kind'],subject=str(msg.get('Subject','')),
        sender=str(msg.get('From','')),date=str(msg.get('Date','Not stated')),message_id=str(msg.get('Message-ID','Not stated')),
        raw_sha256=hashlib.sha256(raw).hexdigest(),text=text,history=history,attachments=manifest,
        publication='Evidence review does not publish dataset rows. Lancaster annual quantity and cooperative coverage remain held.' if any(a['sha256'] in reviews for a in manifest) else 'Evidence review does not publish dataset rows.')
    return result,raw,payloads
