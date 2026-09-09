import re, json, os

FLEET_PATH = 'docs/fleet.json'
FLEET = json.load(open(FLEET_PATH)) if os.path.exists(FLEET_PATH) else {}

# Ident. line format: "Ident.: ABN-26-00010842.0042001 / 26 MAY 26"
# Not anchored to line-start: PDF extraction sometimes glues a stray digit
# (footnote/revision marker) directly before "Ident.:" on the same line
# (e.g. "3Ident.: ABN-26-...").
IDENT_SPLIT_RE = re.compile(r'(?=Ident\.: [A-Z0-9][\w\-]*\.\d+ / \d{2} [A-Z]{3} \d{2})')
IDENT_LINE_RE = re.compile(r'Ident\.: ([A-Z0-9][\w\-]*\.\d+) / (\d{2} [A-Z]{3} \d{2})')
TC_RE = re.compile(r'TC-[A-Z0-9]{3,5}')

# A line that is purely a continuation of a comma-separated "Applicable to:"
# tail-registration list (allows trailing/leading commas and whitespace).
TAIL_CONTINUATION_RE = re.compile(r'^[\sA-Z0-9,\-]*TC-[\sA-Z0-9,\-]*$')


def _extract_applicability(chunk):
    """Find the 'Applicable to:' block (not anchored to line-start — a stray
    footnote digit can be glued directly in front, e.g. '1Applicable to:').
    Merges wrapped continuation lines (long TC- tail lists spanning several
    lines) into one block. Returns (block_text, end_offset_in_chunk)."""
    m = re.search(r'Applicable to:\s*', chunk)
    if not m:
        return None, 0

    line_end = chunk.find('\n', m.end())
    if line_end == -1:
        line_end = len(chunk)
    lines = [chunk[m.end():line_end].strip()]
    pos = line_end + 1

    while pos < len(chunk):
        nxt_end = chunk.find('\n', pos)
        if nxt_end == -1:
            nxt_end = len(chunk)
        stripped = chunk[pos:nxt_end].strip()
        if stripped and TAIL_CONTINUATION_RE.match(stripped):
            lines.append(stripped)
            pos = nxt_end + 1
        else:
            break

    return ' '.join(lines).strip(), pos


def _parse_applicability(block):
    """Returns (kind, values, extra_msns). kind in {'ALL','TAIL','MSN','UNKNOWN'}.

    extra_msns catches bare 'MSN nnnnn' references that trail a TC- tail
    list (aircraft still in the delivery pipeline, referenced directly by
    MSN because they don't have a THY tail registration yet — e.g.
    '...TC-NDZ, MSN 13389, 13426-13594'). These are additive to a TAIL
    result, not an alternative to it.
    """
    if block is None:
        return 'UNKNOWN', [], []

    tails = TC_RE.findall(block)
    if tails:
        extra_msns = re.findall(r'\bMSN\s+(\d{4,6})\b', block)
        return 'TAIL', tails, extra_msns

    upper = block.upper()
    if upper.startswith('ALL') and 'MSN' not in upper:
        return 'ALL', [], []

    if 'MSN' in upper:
        msns = re.findall(r'\bMSN\s+([\d,\-\s]+)', block, re.IGNORECASE)
        nums = []
        for group in msns:
            nums.extend(re.findall(r'\d{4,6}', group))
        if nums:
            return 'MSN', nums, []

    if upper.startswith('ALL'):
        return 'ALL', [], []

    return 'UNKNOWN', [], []


def _msn_tag_lines(kind, values, extra_msns=None):
    lines = []
    if kind == 'TAIL':
        for tc in values:
            entry = FLEET.get(tc)
            if entry and entry.get('msn'):
                lines.append(f"THY MSN {entry['msn']} {tc}")
            else:
                lines.append(f"THY MSN UNKNOWN {tc}")  # not in fleet.json
        for msn in (extra_msns or []):
            lines.append(f"THY MSN {msn}")  # no TC- tail assigned yet
    elif kind == 'MSN':
        for msn in values:
            lines.append(f"THY MSN {msn}")
    return lines


MAX_CHARS = 6000  # conservative safety margin under OpenAI's 8192-token embedding limit
OVERLAP = 200


def _sliding_split(body, size=MAX_CHARS, overlap=OVERLAP):
    windows = []
    start = 0
    n = len(body)
    while start < n:
        end = min(start + size, n)
        windows.append(body[start:end])
        if end == n:
            break
        start += size - overlap
    return windows


def chunk_qrh(text, doc_code='QRH'):
    """Ident.-anchored QRH chunker.

    Returns a list of dicts: {doc_code, chunk_index, content, section,
    applicability_kind, applicability_values}.
    section is one of: 'admin' (front matter before the first Ident.),
    'blank' (Ident. block whose body is just an intentionally-blank page),
    'procedure' (real content).
    """
    parts = IDENT_SPLIT_RE.split(text)
    chunks = []
    idx = 0

    # Segment 0 (before the first "Ident.:") is front-matter / administrative
    # content (Transmittal Letter, Filing Instructions, PLP, cover pages...).
    if parts and parts[0].strip():
        admin_text = parts[0].strip()
        if len(admin_text) > 50:
            for window in _sliding_split(admin_text):
                chunks.append({
                    'doc_code': doc_code,
                    'chunk_index': idx,
                    'content': window,
                    'section': 'admin',
                    'applicability_kind': None,
                    'applicability_values': [],
                })
                idx += 1

    for part in parts[1:]:
        part = part.strip()
        if len(part) < 20:
            continue

        applic_block, applic_end = _extract_applicability(part)
        kind, values, extra_msns = _parse_applicability(applic_block)

        body = part[applic_end:].strip()
        is_blank = len(body) < 40 and 'intentionally left blank' in body.lower()

        tag_lines = _msn_tag_lines(kind, values, extra_msns)
        header = part[:applic_end].strip()
        if tag_lines:
            header = header + '\n\n[MSN TAGS]\n' + '\n'.join(tag_lines)

        full_content = header + ('\n\n' + body if body else '')
        section = 'blank' if is_blank else 'procedure'

        if len(full_content) <= MAX_CHARS or not body:
            chunks.append({
                'doc_code': doc_code,
                'chunk_index': idx,
                'content': full_content,
                'section': section,
                'applicability_kind': kind,
                'applicability_values': values,
            })
            idx += 1
        else:
            # Oversized procedure block (a checklist/table spanning many
            # pages under one Ident.): slide through the body, repeating
            # the Ident./Applicable-to/MSN-tag header on every sub-chunk
            # so applicability context and MSN tagging survive the split.
            budget = max(MAX_CHARS - len(header) - 4, 500)
            for window in _sliding_split(body, size=budget):
                chunks.append({
                    'doc_code': doc_code,
                    'chunk_index': idx,
                    'content': header + '\n\n' + window,
                    'section': section,
                    'applicability_kind': kind,
                    'applicability_values': values,
                })
                idx += 1

    return chunks


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/qrh_abn26_sample.txt'
    text = open(path).read()
    chunks = chunk_qrh(text)

    import statistics
    proc = [c for c in chunks if c['section'] == 'procedure']
    admin = [c for c in chunks if c['section'] == 'admin']
    blank = [c for c in chunks if c['section'] == 'blank']

    print(f'Total chunks: {len(chunks)}  (procedure={len(proc)}, admin={len(admin)}, blank={len(blank)})')
    if proc:
        lens = [len(c['content']) for c in proc]
        print(f'Procedure chunk lengths: median={statistics.median(lens):.0f} min={min(lens)} max={max(lens)}')

    kinds = {}
    for c in proc:
        kinds[c['applicability_kind']] = kinds.get(c['applicability_kind'], 0) + 1
    print('Applicability kinds among procedure chunks:', kinds)

    print()
    print('=== Sample chunks ===')
    shown_all = shown_tail = 0
    for c in proc:
        if c['applicability_kind'] == 'ALL' and shown_all < 1:
            print(f"--- chunk_index {c['chunk_index']} (ALL) ---")
            print(c['content'][:900])
            print()
            shown_all += 1
        if c['applicability_kind'] == 'TAIL' and shown_tail < 1:
            print(f"--- chunk_index {c['chunk_index']} (TAIL, {len(c['applicability_values'])} tails) ---")
            print(c['content'][:1400])
            print('...')
            print(c['content'][-400:])
            print()
            shown_tail += 1
        if shown_all and shown_tail:
            break
