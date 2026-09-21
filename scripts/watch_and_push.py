#!/usr/bin/env python3
"""docs/pdfs/ klasörünü izler; yeni/değişen, bilinen bir doc_code'a ait,
bozuk olmayan bir PDF görürse otomatik commit+push eder (push tetikleyicisi
embed.yml'i devreye sokar). launchd ile arka planda sürekli çalışması için
tasarlandı (bkz. scripts/com.cof.watchandpush.plist, henüz oluşturulmadı).
"""
import os
import signal
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)

import fitz


def handle_sigterm(signum, frame):
    print('[stop] kapatılıyor')
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH_DIR = os.path.join(REPO_ROOT, 'docs', 'pdfs')
POLL_INTERVAL = 5
STABLE_MAX_WAIT = 60  # saniye — bundan uzun süre boyutu değişen dosya "asla stabilize olmadı" sayılır

# CLAUDE.md'nin "## Qdrant Inventory" bölümünden türetildi (2026-09-21),
# FCOM_IMG/FCTM_IMG hariç — onlar kendi PDF dosyası olmayan, FCOM/FCTM'den
# görsel captioning ile türetilen sözde-doc_code'lar. Yeni bir doc_code
# eklenirse (CLAUDE.md + SOURCE_LIMITS güncellendiğinde) burası da elle
# güncellenmeli.
KNOWN_DOC_CODES = {
    'AFM', 'AML', 'AML_Pages', 'CCM', 'De-Icing', 'DGR_Allowed', 'DGR_Guide',
    'Dispatch_Manual', 'EASA_Easy_Access_Rules', 'FCOM', 'FCTM', 'GOM',
    'ICAO_4444', 'ICAO_8168', 'ICAO_8168-IIII', 'ICAO_Annex_2',
    'ICAO_Annex_6-1', 'LVO', 'MEL', 'OM_Part-A', 'OM_Part-B', 'QRH',
    'SHT_OPS',
}


def notify(title, message):
    safe = lambda s: s.replace('\\', '\\\\').replace('"', '\\"')
    script = f'display notification "{safe(message)}" with title "{safe(title)}"'
    try:
        subprocess.run(['osascript', '-e', script], check=False, timeout=10)
    except Exception as e:
        print(f'[warn] macOS bildirimi gönderilemedi: {e}')


def error(doc_label, message):
    print(f'[error] {doc_label}: {message}')
    notify('watch_and_push.py hatası', f'{doc_label}: {message}')


def run_git(args, check=True):
    return subprocess.run(
        ['git'] + args, cwd=REPO_ROOT, check=check,
        capture_output=True, text=True,
    )


def git_status_pdfs():
    """docs/pdfs/ altında değişmiş (yeni/değişmiş/silinmiş) *.pdf dosyalarının
    (status_code, relative_path) listesini döndürür."""
    result = run_git([
        'status', '--porcelain', '--',
        'docs/pdfs/*.pdf', 'docs/pdfs/*.PDF',
    ])
    entries = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status_code = line[:2].strip()
        rel_path = line[3:].strip()
        entries.append((status_code, rel_path))
    return entries


def wait_for_stable_size(path, doc_label):
    waited = 0
    try:
        last_size = os.path.getsize(path)
    except OSError as e:
        error(doc_label, f'dosya boyutu okunamadı: {e}')
        return False

    while waited < STABLE_MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        try:
            size = os.path.getsize(path)
        except OSError as e:
            error(doc_label, f'dosya boyutu okunamadı: {e}')
            return False
        if size == last_size:
            return True
        last_size = size

    error(doc_label, f'dosya boyutu {STABLE_MAX_WAIT}sn içinde stabilize olmadı, atlanıyor')
    return False


def is_valid_pdf(path, doc_label):
    try:
        doc = fitz.open(path)
        page_count = doc.page_count
        doc.close()
        if page_count == 0:
            error(doc_label, 'PDF açıldı ama 0 sayfa içeriyor')
            return False
        return True
    except Exception as e:
        error(doc_label, f'PDF açılamadı (bozuk olabilir): {e}')
        return False


def push_pending_commits():
    ahead = run_git(['rev-list', '--count', '@{u}..HEAD'], check=False)
    if ahead.returncode != 0 or ahead.stdout.strip() in ('', '0'):
        return
    result = run_git(['push'], check=False)
    if result.returncode != 0:
        error('git push', f'bekleyen commit(ler) push edilemedi: {result.stderr.strip()}')
    else:
        print('[ok] Bekleyen commit(ler) başarıyla push edildi')


def commit_and_push(rel_path, doc_code):
    abs_path = os.path.join(REPO_ROOT, rel_path)
    msg = f'Update {doc_code}.pdf via watch_and_push.py (auto-detected content change)'

    add_result = run_git(['add', rel_path], check=False)
    if add_result.returncode != 0:
        error(doc_code, f'git add başarısız: {add_result.stderr.strip()}')
        return

    commit_result = run_git(['commit', '-m', msg, '--', rel_path], check=False)
    if commit_result.returncode != 0:
        error(doc_code, f'git commit başarısız: {commit_result.stderr.strip()}')
        return

    push_result = run_git(['push'], check=False)
    if push_result.returncode != 0:
        error(doc_code, f'git push başarısız (commit lokalde kaldı, bir sonraki döngüde tekrar denenecek): {push_result.stderr.strip()}')
        return

    print(f'[ok] {doc_code}: commit edildi ve push edildi')


def process_pdfs():
    for status_code, rel_path in git_status_pdfs():
        if status_code == 'D':
            continue

        filename = os.path.basename(rel_path)
        doc_code = os.path.splitext(filename)[0]

        if doc_code not in KNOWN_DOC_CODES:
            error(doc_code, f'bilinmeyen doc_code — dosya adı "{filename}", bilinen 23 doc_code listesinde yok')
            continue

        abs_path = os.path.join(REPO_ROOT, rel_path)

        if not wait_for_stable_size(abs_path, doc_code):
            continue

        if not is_valid_pdf(abs_path, doc_code):
            continue

        commit_and_push(rel_path, doc_code)


def main():
    print(f'[start] docs/pdfs/ izleniyor ({WATCH_DIR}), {POLL_INTERVAL}sn polling')
    while True:
        push_pending_commits()
        process_pdfs()
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
