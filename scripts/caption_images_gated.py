"""
caption_images_gated.py

FCOM/FCTM metni her yeniden embed edildiğinde (bkz. embed.yml dispatcher),
görsel captioning'i (image_captioning_rag.py) OTOMATİK olarak tetiklemeden
önce üç güvenlik kapısından geçirir:

  1) Sayfa-hash karşılaştırması: image_captioning_rag.py'nin sayfa-numarası
     bazlı checkpoint'i, "sayfa 500 zaten işlendi" der ama PDF revize
     edildiğinde o sayfanın İÇERİĞİ değişmiş olabilir — checkpoint bunu
     bilmez. Bu script her sayfanın render edilmiş görselinin SHA256
     hash'ini ayrı bir dosyada (.page_hashes_v1.json) tutar; hash değişmemiş
     sayfalar Claude'a hiç sorulmadan (ücretsiz) atlanır, sadece GERÇEKTEN
     değişen/yeni sayfalar yeniden captioning'e girer.

  2) Sayfa tavanı (MAX_CHANGED_PAGES): bu çalıştırmada işlenecek
     (hash'i değişmiş + görsel içerik barındıran) sayfa sayısı bu tavanı
     aşarsa, HİÇ BİR SAYFA İŞLENMEDEN (sıfır API maliyeti) çıkılır —
     "bu kadar çok şey birden değişmiş, muhtemelen bir şeyler yanlış" sinyali.

  3) Aylık harcama tavanı (MONTHLY_BUDGET_USD): .monthly_image_caption_spend.json
     dosyasındaki güncel ayın tahmini harcaması bu tavanı zaten aşmışsa,
     yine hiçbir sayfa işlenmeden çıkılır. Çalışma sırasında da her sayfadan
     sonra harcama güncellenir ve tavana yaklaşınca çalışma temiz şekilde
     durur (kalan sayfalar sonraki çalıştırmaya kalır).

Bu script image_captioning_rag.py'yi DEĞİŞTİRMEZ — onun düşük seviye
fonksiyonlarını (render_page, page_has_content, generate_caption, embed_text,
upsert_point, checkpoint yardımcıları) import edip kendi hash-farkı ve
maliyet-kapısı mantığıyla sarar. Başarıyla işlenen sayfalar, hem bu
script'in kendi hash checkpoint'ine HEM DE image_captioning_rag.py'nin
kendi 'done' checkpoint'ine yazılır — böylece ikisi arasında tutarsızlık
oluşmaz (biri manuel çalıştırılırsa diğerinin yaptığı işi tekrar etmez).

Henüz embed.yml dispatcher'ına bağlanmadı, henüz çalıştırılmadı.
"""

import hashlib
import json
import base64
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from anthropic import Anthropic
from openai import OpenAI
from qdrant_client import QdrantClient

# generate_caption() import edilMİYOR kasıtlı olarak: sadece caption
# string'ini döndürüyor, response.usage (maliyet takibi için gereken
# input/output token sayıları) döndürmüyor. Bu yüzden aynı CLAUDE_MODEL +
# CAPTION_SYSTEM ile API çağrısını burada, usage'a erişecek şekilde
# yeniden yapıyoruz (mantık kopyası değil, sadece dönüş değeri farklı).
from image_captioning_rag import (  # noqa: E402
    PDF_MAP,
    CLAUDE_MODEL,
    CAPTION_SYSTEM,
    render_page,
    page_has_content,
    extract_msn_range,
    embed_text,
    upsert_point,
    ensure_collection,
    load_checkpoint,
    save_checkpoint,
)

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

HASH_CHECKPOINT_FILE = Path("docs/pdfs/.page_hashes_v1.json")
SPEND_FILE = Path("docs/pdfs/.monthly_image_caption_spend.json")

# Bu çalıştırmada işlenecek (hash'i değişmiş + görsel içerikli) sayfa sayısı
# bunu aşarsa hiçbir şey işlenmez. "Toplam PDF sayfası" değil, "gerçekten
# değişen sayfa sayısı" — bu yüzden FCOM (7.022 sayfa) ve FCTM (542 sayfa)
# gibi büyük dokümanlar, sadece birkaç sayfası değiştiğinde sorunsuz çalışır.
MAX_CHANGED_PAGES = 500

# Aylık toplam tahmini harcama tavanı (USD). Bu değer aşıldığında o ayın
# geri kalanında hiçbir otomatik captioning çalışmaz (yeni ay gelene kadar).
MONTHLY_BUDGET_USD = 150.0

# claude-sonnet-5 fiyatlandırması (image_captioning_rag.py'nin CLAUDE_MODEL
# sabitiyle senkron tutulmalı — model değişirse burası da güncellenmeli).
PRICE_IN_PER_M = 2.00
PRICE_OUT_PER_M = 10.00


def current_month_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_spend() -> dict:
    if SPEND_FILE.exists():
        try:
            data = json.loads(SPEND_FILE.read_text())
        except Exception:
            data = {}
    else:
        data = {}

    month = current_month_str()
    if data.get("month") != month:
        # Yeni ay — harcama otomatik olarak sıfırlanır.
        data = {"month": month, "spend_usd": 0.0}
    data.setdefault("spend_usd", 0.0)
    return data


def save_spend(data: dict) -> None:
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps(data, indent=2))


def load_page_hashes() -> dict:
    if HASH_CHECKPOINT_FILE.exists():
        try:
            return json.loads(HASH_CHECKPOINT_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_page_hashes(data: dict) -> None:
    HASH_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))


def hash_page(png_bytes: bytes) -> str:
    return hashlib.sha256(png_bytes).hexdigest()


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * PRICE_IN_PER_M + (output_tokens / 1_000_000) * PRICE_OUT_PER_M


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/caption_images_gated.py <FCOM|FCTM>")
        sys.exit(1)

    doc_code = sys.argv[1].strip().upper()
    if doc_code not in PDF_MAP:
        print(f"[error] Desteklenmeyen doc_code: {doc_code} "
              f"(image_captioning_rag.py PDF_MAP'inde sadece: {', '.join(PDF_MAP.keys())})")
        sys.exit(1)

    pdf_path = PDF_MAP[doc_code]
    if not pdf_path.exists():
        print(f"[error] {pdf_path} bulunamadı")
        sys.exit(1)

    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "QDRANT_URL": QDRANT_URL,
    }.items() if not v]
    if missing:
        print(f"[error] Eksik env var: {', '.join(missing)}")
        sys.exit(1)

    import fitz  # PyMuPDF

    # --- Aşama 1: hash farkına göre değişen/yeni sayfaları bul (ücretsiz) ---
    print(f"[hash] {doc_code}: sayfa hash'leri hesaplanıyor (render + SHA256, API çağrısı yok)...")
    all_hashes = load_page_hashes()
    doc_hashes = all_hashes.get(doc_code, {})
    new_doc_hashes = dict(doc_hashes)  # bu run sonunda güncellenecek kopya

    doc = fitz.open(str(pdf_path))
    total_pages = doc.page_count
    changed_pages = []  # [(page_index, page_num, png_bytes, new_hash)]

    for page_index in range(total_pages):
        page_num = page_index + 1
        page = doc.load_page(page_index)

        if not page_has_content(page):
            continue  # görsel içerik yok — hash'e bile gerek yok, hep ücretsiz atlanır

        png_bytes = render_page(page)
        new_hash = hash_page(png_bytes)
        old_hash = doc_hashes.get(str(page_num))

        if old_hash == new_hash:
            continue  # sayfa değişmemiş — Claude'a hiç sorulmadı

        changed_pages.append((page_index, page_num, png_bytes, new_hash))

    doc.close()

    print(f"[hash] {doc_code}: toplam {total_pages} sayfa, "
          f"{len(changed_pages)} sayfa yeni/değişmiş (görsel içerikli olanlar arasında)")

    if not changed_pages:
        print(f"[done] {doc_code}: hiçbir sayfa değişmemiş, yapılacak bir şey yok.")
        return

    # --- Aşama 2: sayfa tavanı kontrolü (hâlâ ücretsiz) ---
    if len(changed_pages) > MAX_CHANGED_PAGES:
        print(f"[abort] {doc_code}: {len(changed_pages)} değişen sayfa, "
              f"MAX_CHANGED_PAGES={MAX_CHANGED_PAGES} tavanını aşıyor.")
        print("        Hiçbir sayfa işlenmedi (sıfır API maliyeti). "
              "Bu kadar çok değişiklik beklenmiyor — elle inceleyip "
              "gerekirse scripts/image_captioning_rag.py'yi manuel çalıştırın.")
        sys.exit(0)  # bilinçli güvenlik durdurması, hata değil

    # --- Aşama 3: aylık bütçe kontrolü (hâlâ ücretsiz) ---
    spend = load_spend()
    if spend["spend_usd"] >= MONTHLY_BUDGET_USD:
        print(f"[abort] Bu ayki ({spend['month']}) bütçe (${MONTHLY_BUDGET_USD:.2f}) "
              f"zaten aşılmış (${spend['spend_usd']:.2f} harcanmış). "
              "Hiçbir sayfa işlenmedi. Yeni ayda otomatik sıfırlanacak.")
        sys.exit(0)  # bilinçli güvenlik durdurması, hata değil

    print(f"[budget] Bu ay ({spend['month']}) şu ana kadar: ${spend['spend_usd']:.2f} / "
          f"${MONTHLY_BUDGET_USD:.2f}")

    # --- Aşama 4: gerçek captioning (API maliyeti burada başlıyor) ---
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    qdrant_kwargs = {"url": QDRANT_URL}
    if QDRANT_API_KEY:
        qdrant_kwargs["api_key"] = QDRANT_API_KEY
    qdrant = QdrantClient(**qdrant_kwargs)
    ensure_collection(qdrant)

    legacy_done = load_checkpoint()  # image_captioning_rag.py'nin kendi 'done' seti

    doc = fitz.open(str(pdf_path))
    processed = 0
    stopped_on_budget = False

    for page_index, page_num, png_bytes, new_hash in changed_pages:
        spend = load_spend()  # her sayfada tazele (ay değişmiş olabilir)
        if spend["spend_usd"] >= MONTHLY_BUDGET_USD:
            print(f"[stop] Aylık bütçe çalışma sırasında aşıldı "
                  f"(${spend['spend_usd']:.2f}/${MONTHLY_BUDGET_USD:.2f}). "
                  f"Kalan {len(changed_pages) - processed} sayfa sonraki çalıştırmaya kalıyor.")
            stopped_on_budget = True
            break

        page = doc.load_page(page_index)
        page_text = page.get_text("text")
        msn_range = extract_msn_range(page)

        print(f"  [caption] {doc_code} sayfa {page_num}/{total_pages} "
              f"(MSN: {msn_range or 'N/A'})...")

        b64_data = base64.standard_b64encode(png_bytes).decode()
        try:
            response = anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=512,
                system=CAPTION_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                       "data": b64_data}},
                        {"type": "text", "text": f"Document: {doc_code}, Page: {page_num}.\n"
                                                  f"Page text context: {page_text[:600] if page_text else 'N/A'}\n\n"
                                                  "Write a technical caption for this page, or respond SKIP if it is a logo/header/cover page."},
                    ],
                }],
            )
        except Exception as e:
            print(f"    [warn] Caption çağrısı başarısız: {e}; bu sayfa atlanıyor.")
            continue

        cost = estimate_cost_usd(response.usage.input_tokens, response.usage.output_tokens)
        spend["spend_usd"] += cost
        save_spend(spend)

        caption = response.content[0].text.strip() if response.content else ""

        point_key = f"{doc_code}_v2_p{page_num}"

        if caption.upper() == "SKIP":
            print(f"    [skip] Logo/header sayfası — yazılmadı. (+${cost:.4f})")
            new_doc_hashes[str(page_num)] = new_hash
            legacy_done.add(point_key)
            processed += 1
            continue

        try:
            vector = embed_text(openai_client, caption)
        except Exception as e:
            print(f"    [warn] Embedding başarısız: {e}; bu sayfa atlanıyor.")
            continue

        payload = {
            "doc_code": f"{doc_code}_IMG",
            "has_image": True,
            "image_b64": b64_data,
            "msn_range": msn_range,
            "page_num": page_num,
            "source_ref": f"{doc_code} p.{page_num}",
            "caption": caption,
        }

        try:
            upsert_point(qdrant, point_key, vector, payload)
        except Exception as e:
            print(f"    [warn] Qdrant upsert başarısız: {e}; bu sayfa atlanıyor.")
            continue

        new_doc_hashes[str(page_num)] = new_hash
        legacy_done.add(point_key)
        processed += 1
        print(f"    [ok] Sayfa {page_num} yazıldı. (+${cost:.4f}, toplam ay: ${spend['spend_usd']:.2f})")

        time.sleep(0.1)

    doc.close()

    all_hashes[doc_code] = new_doc_hashes
    save_page_hashes(all_hashes)
    save_checkpoint(legacy_done)  # image_captioning_rag.py'nin checkpoint'iyle senkron kal

    print()
    print(f"[done] {doc_code}: {processed}/{len(changed_pages)} değişen sayfa işlendi"
          + (" (bütçe nedeniyle erken durduruldu)" if stopped_on_budget else ""))
    print(f"[done] Bu ayki ({spend['month']}) toplam tahmini harcama: ${spend['spend_usd']:.2f} / ${MONTHLY_BUDGET_USD:.2f}")


if __name__ == "__main__":
    main()
