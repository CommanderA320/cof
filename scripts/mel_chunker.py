"""
mel_chunker.py

MEL (Minimum Equipment List) icin Ident.-anchored chunker - qrh_chunker_ident.py
ile ayni applicability-parse mantigini kullanir, MEL'in kendi baslik formatina
uyarlanmis.

MEL.pdf'te IKI farkli item alt-formati gozlemlendi:
  1) "MI-" (equipment-based) - acik bir ATA kodu satiri var:
        22-10-02
        Flight Director (FD)
        Ident.: MI-22-10-00007530.0001001 / 16 MAY 19
        Applicable to: TC-JLS, TC-JLT, ...
  2) "ME-" (ECAM-alert-based) - ATA kodu satiri YOK, sadece ECAM alert adi var:
        ECAM Alert: T.O SPEEDS NOT INSERTED
        Ident.: ME-22-00016087.9001002 / 02 APR 26
        Applicable to: TC-JTV, TC-JTY

Her iki formatta da "Ident.: " isaretleyicisi evrensel - bu yuzden QRH
chunker'indaki gibi asil split noktasi budur (ATA kodu satiri degil, cunku
sadece MI- tipinde var). Her segment icin:
  - Once hemen oncesindeki baglamda acik bir "dd-dd-dd" ATA kodu satiri aranir
    (MI- tipi) - varsa bu, en spesifik ata_ref olarak kullanilir.
  - Yoksa Ident.'in kendi oneginden (ME-22-... / MI-22-10-...) ATA bolumu
    (chapter, ve varsa alt-bolum) cikarilip ata_ref olarak kullanilir (ME-
    tipi icin sadece 2 haneli chapter, orn. "22").
  - Oncesindeki metin (ATA kodu haric) item title/context olarak alinir
    (MI- icin ekipman adi, ME- icin ECAM Alert satiri).

Sorun: "Applicable to: TC-..." tescil listeleri chunk'in buyuk kismini
(gozlemlenen orneklerde >%90) kapliyor ve embedding vektorunu kirletiyor -
canli arama testinde tescil-listesi-agirlikli chunk'larin gercek teknik
icerikten daha yuksek similarity aldigi gosterildi.

Cozum: Applicability blogu embed edilecek metinden CIKARILIR, ayri bir
payload alanina (applicability_tails) konur, content'e sadece kisa bir ozet
satiri eklenir. ata_ref payload alani da bu firsatta doldurulur (su ana
kadar 0/36.079 nokta doluydu).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from qrh_chunker_ident import (  # noqa: E402
    FLEET,
    IDENT_SPLIT_RE,
    _extract_applicability,
    _parse_applicability,
    _sliding_split,
)

MAX_CHARS = 6000

# MI- tipi: acik "dd-dd-dd" ATA kodu satiri, hemen Ident.'ten onceki baglamda.
ATA_LINE_RE = re.compile(r'^(\d{2}-\d{2}-\d{2}[A-Z]?)\s*$', re.MULTILINE)

# Ident.'in kendi oneki: ME-22-... veya MI-22-10-... -> chapter (+ varsa subsection)
IDENT_PREFIX_RE = re.compile(r'^(ME|MI)-(\d{2})(?:-(\d{2}))?-')

PUA_LOW = 0xE000
PUA_HIGH = 0xF8FF


def _clean(s):
    """PDF'e ozel/anlamsiz glyph'leri (Private Use Area, checkbox vb.) at."""
    return ''.join(ch for ch in s if not (PUA_LOW <= ord(ch) <= PUA_HIGH)).strip()


def _extract_header(preceding_text):
    """Bir 'Ident.: ' isaretinden hemen onceki baglamdan (son ~300 karakter)
    ata_ref (fine-grained varsa) ve title/context cikarir."""
    tail = preceding_text[-300:]
    lines = [_clean(l) for l in tail.split('\n')]
    lines = [l for l in lines if l]

    # MI- tipi: son satirlardan biri saf bir ATA kodu mu? Varsa title, o
    # satirla Ident. arasindaki satir(lar).
    for i in range(len(lines) - 1, -1, -1):
        m = ATA_LINE_RE.match(lines[i])
        if m:
            title = ' '.join(lines[i + 1:])
            return m.group(1), title[:150]

    # ME- tipi: en yakin "ECAM Alert: ..." satirini title olarak al -
    # rastgele "son N satir" yerine spesifik olarak bu deseni ariyoruz ki
    # onceki item'in govde metnini/sayfa altbilgisini (tarih, vb.) yakalamayalim.
    for line in reversed(lines):
        if line.startswith('ECAM Alert:'):
            return None, line[:150]

    # Ne ATA satiri ne ECAM Alert bulunabildi - en yakin anlamli satiri kullan
    return None, (lines[-1][:150] if lines else '')


def _ata_from_ident_prefix(ident):
    m = IDENT_PREFIX_RE.match(ident)
    if not m:
        return None
    chapter, sub = m.group(2), m.group(3)
    return f'{chapter}-{sub}' if sub else chapter


def _applicability_summary(kind, values, extra_msns=None):
    """Tam listeyi tekrar embed etmeden, bilgiyi tamamen de atmadan kisa bir ozet satiri."""
    extra_msns = extra_msns or []
    if kind == 'ALL':
        return '[Applicable: ALL aircraft]'
    if kind == 'TAIL':
        n = len(values) + len(extra_msns)
        return f'[Applicable: {n} tails/MSNs - see payload.applicability_tails]'
    if kind == 'MSN':
        return f'[Applicable: {len(values)} aircraft by MSN - see payload.applicability_tails]'
    return '[Applicability: not specified in source]'


IDENT_LINE_RE = re.compile(r'Ident\.: ([A-Z0-9][\w\-]*\.\d+) / (\d{2} [A-Z]{3} \d{2})')


def chunk_mel(text, doc_code='MEL'):
    """Ident.-anchored MEL chunker.

    Returns a list of dicts: {doc_code, chunk_index, content, section,
    ata_ref, item_title, applicability_kind, applicability_tails}.
    section is 'admin' (ilk item'dan onceki front-matter) veya 'item'.
    """
    matches = list(IDENT_SPLIT_RE.finditer(text))
    chunks = []
    idx = 0

    # Ilk "Ident." isaretinden onceki front-matter (Preliminary Pages, vb.)
    admin_end = matches[0].start() if matches else len(text)
    admin_text = text[:admin_end].strip()
    if len(admin_text) > 50:
        for window in _sliding_split(admin_text):
            chunks.append({
                'doc_code': doc_code, 'chunk_index': idx, 'content': window,
                'section': 'admin', 'ata_ref': None, 'item_title': None,
                'applicability_kind': None, 'applicability_tails': [],
            })
            idx += 1

    for i, m in enumerate(matches):
        seg_start = m.start()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        part = text[seg_start:seg_end]

        ident_m = IDENT_LINE_RE.match(part)
        if not ident_m:
            continue
        ident, date = ident_m.group(1), ident_m.group(2)

        preceding = text[max(0, seg_start - 300):seg_start]
        ata_ref, title = _extract_header(preceding)
        if ata_ref is None:
            ata_ref = _ata_from_ident_prefix(ident)

        rest = part[ident_m.end():]
        applic_block, applic_end = _extract_applicability(rest)
        kind, values, extra_msns = _parse_applicability(applic_block)
        summary = _applicability_summary(kind, values, extra_msns)

        body = _clean(rest[applic_end:])

        header_line = f'{ata_ref or "UNKNOWN"}\n{title}\nIdent.: {ident} / {date}'
        full_content = header_line + '\n' + summary + ('\n\n' + body if body else '')
        applicability_tails = list(values) + list(extra_msns)

        if len(full_content) <= MAX_CHARS or not body:
            chunks.append({
                'doc_code': doc_code, 'chunk_index': idx, 'content': full_content,
                'section': 'item', 'ata_ref': ata_ref, 'item_title': title,
                'applicability_kind': kind, 'applicability_tails': applicability_tails,
            })
            idx += 1
        else:
            fixed_header = header_line + '\n' + summary
            budget = max(MAX_CHARS - len(fixed_header) - 4, 500)
            for window in _sliding_split(body, size=budget):
                chunks.append({
                    'doc_code': doc_code, 'chunk_index': idx,
                    'content': fixed_header + '\n\n' + window,
                    'section': 'item', 'ata_ref': ata_ref, 'item_title': title,
                    'applicability_kind': kind, 'applicability_tails': applicability_tails,
                })
                idx += 1

    return chunks


if __name__ == '__main__':
    import statistics

    path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/mel_sample.txt'
    text = open(path).read()
    chunks = chunk_mel(text)

    items = [c for c in chunks if c['section'] == 'item']
    admin = [c for c in chunks if c['section'] == 'admin']

    print(f'Total chunks: {len(chunks)} (item={len(items)}, admin={len(admin)})')
    if items:
        lens = [len(c['content']) for c in items]
        print(f'Item chunk lengths: median={statistics.median(lens):.0f} min={min(lens)} max={max(lens)}')
        with_ata = sum(1 for c in items if c['ata_ref'] and c['ata_ref'] != 'UNKNOWN')
        print(f'ata_ref doldurulan chunk: {with_ata}/{len(items)}')

    kinds = {}
    for c in items:
        kinds[c['applicability_kind']] = kinds.get(c['applicability_kind'], 0) + 1
    print('Applicability kinds:', kinds)

    print()
    print('=== Ornek chunklar ===')
    for c in items[:6]:
        print(f"--- ata_ref={c['ata_ref']} title={c['item_title']!r} kind={c['applicability_kind']} tails={len(c['applicability_tails'])} ---")
        print(c['content'][:500])
        print()
