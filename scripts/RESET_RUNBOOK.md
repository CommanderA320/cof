# Qdrant "documents" koleksiyonu — Tam Reset Runbook

Hazırlık tarihi: 2026-09-11. Bu doküman sadece **planlama/dokümantasyon** amaçlıdır — hiçbir embed/silme işlemi bu doküman yazılırken çalıştırılmadı. Her adım manuel onayla, sırayla çalıştırılmalıdır.

Repo notu: Bu API'yi sunan kod `cofbackend` reposunda, embed script'leri + kaynak PDF'ler + `fleet.json` ise **bu repoda (`cof`)** yaşıyor. Tüm komutlar `~/Developer/GitHub/cof` içinden çalıştırılmalı.

---

## ⚠️ Kritik ön bulgular (reset öncesi mutlaka okunmalı)

### 1. Git tracking boşluğu — script kaybı riski
`scripts/` içindeki 6 dosyadan sadece **2'si commit edilmiş** (`embed_qrh_ident.py`, `qrh_chunker_ident.py` — commit `b388e52`). Şunlar **untracked**:
- `scripts/embed_doc.py` ⚠️ (en çok kullanılan script, 20 doc_code için gerekli)
- `scripts/embed_dispatch_manual.py`
- `scripts/image_captioning_rag.py`
- `scripts/qrh_chunk_fix_v2.py`
- `qrh_add_payload.py` (repo kökünde)

Reset'e başlamadan önce bunları commit etmeyi düşünün — bu runbook'un kapsamı dışında, ayrı bir onay gerektirir.

### 2. Image-captioning checkpoint tuzağı
`image_captioning_rag.py`, `docs/pdfs/.image_caption_checkpoint_v2.json` dosyasında "işlenmiş" sayfaları takip eder ve onları **atlar**. Bu oturumda Qdrant'tan FCOM_IMG'de 6.245, FCTM_IMG'de 540 duplike nokta silindi — ama checkpoint dosyası bu sayfaları hâlâ "done" sanıyor. **Script'i olduğu gibi tekrar çalıştırmak hiçbir şey yapmaz.** Tam reset için checkpoint dosyasının silinmesi/taşınması gerekir (bkz. Adım 4).

### 3. FCTM_IMG'yi üreten orijinal script kayıp
`image_captioning_rag.py`'nin mevcut (v2) sürümü `PDF_MAP`'inde sadece FCOM var:
```python
PDF_MAP = {
    "FCOM": Path("docs/pdfs/FCOM.pdf"),
    # "FCTM": Path("docs/pdfs/FCTM.pdf"),  # already done, skip
}
```
FCTM_IMG'yi üreten orijinal (v1) çalıştırma artık `scripts/` içinde yok — sadece eski checkpoint kalıntısı (`docs/pdfs/.image_caption_checkpoint.json`, 9 May) duruyor, script'in kendisi yok. FCTM_IMG'yi sıfırdan yeniden üretmek için v2 script'in `PDF_MAP`'ine FCTM'i geri eklemek gerekir — bu bir **kod değişikliği**, "olduğu gibi çalıştır" değil.

### 4. Logo-duplication hatası muhtemelen tekrar oluşur
Script'in SKIP mantığı ("sadece logo/header sayfasıysa embed etme") var ama pratikte binlerce sayfa aynı jenerik header görselini paylaşıyor ve yine de görsel-alakasız, hallüsine teknik caption'larla embed edilmiş (bu oturumda temizlediğimiz 6.785 nokta). Script değiştirilmeden yeniden çalıştırılırsa **aynı sorun muhtemelen tekrar oluşur**. Öneri: script'e image-hash bazlı bir dedup/skip eklemek (bu runbook kapsamında değiştirilmedi, sadece gözlem).

### 5. `qrh_add_payload.py` — kullanılmıyor, atlanabilir
QRH noktalarına `qrh_ref`/`msn`/`reg` payload alanları ekliyor ve index oluşturuyor. Ancak `api/search.js` içinde (`cofbackend` reposu) bu alanlar **hiç kullanılmıyor** — grep ile doğrulandı. `_buildQRHContext` ve `filterFCOMByMSN`, `content` metnini doğrudan regex/full-text ile tarıyor. Bu script legacy/gereksiz görünüyor — reset akışına dahil etmeye gerek yok.

### 6. `qrh_chunk_fix_v2.py` — legacy, gereksiz
Eski (naive sliding-window) QRH chunk'larında 10.000+ karakterlik olanları düzeltmek içindi (9 Mayıs). Yeni `qrh_chunker_ident.py` chunk'ları zaten 6.000 karakterle sınırlıyor (`MAX_CHARS`) — yeni pipeline'da bu script'e gerek yok.

---

## Doc_code → Script eşleştirmesi (tam liste, 2026-09-11 itibarıyla Qdrant'taki chunk sayılarıyla)

| doc_code | chunk sayısı | script | not |
|---|---|---|---|
| FCOM | 12.959 | `embed_doc.py FCOM` | dahili bölüm-bazlı chunking var |
| QRH | 834 | `embed_qrh_ident.py` | **embed_doc.py DEĞİL** — ident-anchored, MSN/tail-aware |
| MEL | 2.569 | `embed_doc.py MEL` | genel sliding-window |
| FCTM | 657 | `embed_doc.py FCTM` | genel sliding-window |
| AFM | 1.978 | `embed_doc.py AFM` | |
| OM_Part-A | 1.213 | `embed_doc.py OM_Part-A` | |
| OM_Part-B | 205 | `embed_doc.py OM_Part-B` | |
| CCM | 864 | `embed_doc.py CCM` | |
| GOM | 1.304 | `embed_doc.py GOM` | |
| LVO | 8 | `embed_doc.py LVO` | küçük, pipeline testi için iyi aday |
| ICAO_4444 | 983 | `embed_doc.py ICAO_4444` | |
| ICAO_8168 | 243 | `embed_doc.py ICAO_8168` | |
| ICAO_Annex_2 | 65 | `embed_doc.py ICAO_Annex_2` | |
| ICAO_8168-IIII | 156 | `embed_doc.py ICAO_8168-IIII` | |
| ICAO_Annex_6-1 | 184 | `embed_doc.py ICAO_Annex_6-1` | |
| EASA_Easy_Access_Rules | 710 | `embed_doc.py EASA_Easy_Access_Rules` | 2000 karakter chunk (EASA özel) |
| Dispatch_Manual | 199 | `embed_doc.py Dispatch_Manual` | `embed_dispatch_manual.py` da aynı işi yapıyor (hardcoded), ikisi de eşdeğer — birini seç |
| AML | 63 | `embed_doc.py AML` | |
| De-Icing | 464 | `embed_doc.py De-Icing` | |
| AML_Pages | 26 | `embed_doc.py AML_Pages` | |
| DGR_Allowed | 42 | `embed_doc.py DGR_Allowed` | |
| DGR_Guide | 7 | `embed_doc.py DGR_Guide` | en küçük, ilk test için iyi aday |
| SHT_OPS | 760 | `embed_doc.py SHT_OPS` | |
| **FCOM_IMG** | 9.026 | `image_captioning_rag.py` | ayrı süreç, bkz. aşağı |
| **FCTM_IMG** | 128 | *(script kayıp — bkz. bulgu #3)* | v2'ye FCTM eklenmeli |

---

## Görsel koleksiyonlar (FCOM_IMG, FCTM_IMG) — neden ayrı bir plan gerekiyor

- **Maliyet kalemi OpenAI değil, Claude Sonnet vision API'si.** Her sayfa için 1 tam-sayfa render + 1 Claude vision çağrısı (`claude-sonnet-4-6`, caption üretimi) yapılıyor. FCOM.pdf ~6.240+ sayfa → tam resetlemede ~6.240 vision çağrısı, muhtemelen tüm reset sürecinin en pahalı adımı. OpenAI sadece kısa caption metnini embed ediyor (`text-embedding-3-small`), bu kısım ucuz.
- **Checkpoint nedeniyle kısmi/artımlı reset kolay değil** — script sayfa bazlı checkpoint tutuyor (bulgu #2), Qdrant'tan nokta silmek checkpoint'i etkilemiyor.
- **FCTM_IMG için script eksik** (bulgu #3) — kod değişikliği gerektiriyor.
- **Öneri:** Görsel koleksiyonları tam reset senaryosunda metin dokümanlarından **ayrı, kendi onayını gerektiren** bir adım olarak ele alın. Aynı "toplu komut" ile karıştırmayın; maliyet ve süre çok farklı.

---

## Adım Adım Runbook

### Ön koşullar
```bash
cd ~/Developer/GitHub/cof
```
- `.env` dosyasında `QDRANT_URL`, `QDRANT_API_KEY`, `OPENAI_API_KEY` (görseller için ayrıca `ANTHROPIC_API_KEY`) tanımlı olmalı.
- Python bağımlılıkları: `fitz` (PyMuPDF), `openai`, `qdrant-client`, `python-dotenv`, (görseller için) `anthropic`.
- `docs/pdfs/` altında ilgili PDF mevcut olmalı (tüm 24 metin dokümanı için PDF'ler doğrulandı, mevcut).

### Adım 1 — Standart metin dokümanları
`embed_doc.py` argümansız çalışmaz — tek seferde tek `doc_code` işler, dahili olarak o doc_code'un **mevcut Qdrant noktalarını önce siler**, sonra yeniden embed eder (doğal "reset" davranışı — ayrı bir silme adımına gerek yok).

**QRH'yi BURADA çalıştırmayın** (embed_doc.py'nin QRH mantığı var ama eski/basit; Adım 2'deki özel chunker kullanılmalı).

```bash
# Küçükten büyüğe, pipeline'ı doğrulamak için önce küçükleri çalıştırın:
python3 scripts/embed_doc.py DGR_Guide
python3 scripts/embed_doc.py LVO
python3 scripts/embed_doc.py ICAO_Annex_2
python3 scripts/embed_doc.py AML_Pages
python3 scripts/embed_doc.py DGR_Allowed
python3 scripts/embed_doc.py AML
python3 scripts/embed_doc.py ICAO_8168-IIII
python3 scripts/embed_doc.py ICAO_Annex_6-1
python3 scripts/embed_doc.py ICAO_8168
python3 scripts/embed_doc.py OM_Part-B
python3 scripts/embed_doc.py Dispatch_Manual
python3 scripts/embed_doc.py De-Icing
python3 scripts/embed_doc.py SHT_OPS
python3 scripts/embed_doc.py ICAO_4444
python3 scripts/embed_doc.py CCM
python3 scripts/embed_doc.py OM_Part-A
python3 scripts/embed_doc.py FCTM
python3 scripts/embed_doc.py MEL
python3 scripts/embed_doc.py GOM
python3 scripts/embed_doc.py AFM
python3 scripts/embed_doc.py EASA_Easy_Access_Rules
python3 scripts/embed_doc.py FCOM   # en büyük, en son
```
Her komuttan sonra `doc-verifier` subagent'ı ile o `doc_code` için doğrulama önerilir.

### Adım 2 — QRH (özel ident-anchored chunker, MSN/tail-aware)
```bash
python3 scripts/embed_qrh_ident.py
```
Argüman almaz; `docs/pdfs/QRH.pdf` ve `docs/fleet.json` sabit yollardan okunur. Kendi içinde mevcut QRH noktalarını siler, yeniden yazar.

### Adım 3 — (Atlanabilir) `qrh_add_payload.py`
`api/search.js` tarafından kullanılmadığı doğrulandı (bulgu #5) — çalıştırmaya gerek yok. Yine de çalıştırılırsa zarar vermez, sadece gereksiz.

### Adım 4 — Görsel koleksiyonlar (AYRI ONAY GEREKTİRİR)

**FCOM_IMG:**
```bash
# 1) Checkpoint'i temizle/yedekle (yoksa script hiçbir sayfayı işlemez):
mv docs/pdfs/.image_caption_checkpoint_v2.json docs/pdfs/.image_caption_checkpoint_v2.json.bak

# 2) Script'i çalıştır (uzun sürer, yüksek maliyetli — Claude vision + OpenAI):
python3 scripts/image_captioning_rag.py
```

**FCTM_IMG:**
Script'in `PDF_MAP`'ine FCTM'in eklenmesi gerekiyor (şu an yorum satırında kapalı, bulgu #3):
```python
PDF_MAP = {
    "FCOM": Path("docs/pdfs/FCOM.pdf"),
    "FCTM": Path("docs/pdfs/FCTM.pdf"),  # <- yorum kaldırılmalı
}
```
Bu bir kod değişikliği gerektirir — bu runbook'u hazırlarken script'e dokunulmadı, uygulama ayrı bir onay adımı olmalı.

### Adım 5 — Doğrulama
Her `doc_code` için `doc-verifier` subagent'ını çalıştırın (Qdrant count + `/api/search` canlı testi, `https://cofbackend.vercel.app` üzerinden).

---

## Önerilen genel sıralama
1. **Küçük/ucuz dokümanlar** (DGR_Guide, LVO, ICAO_Annex_2, AML_Pages...) — pipeline'ı doğrulamak için önce
2. **Orta/büyük metin dokümanları** (MEL, GOM, AFM, EASA, FCOM)
3. **QRH** (ident chunker, `fleet.json` bağımlılığı doğrulanmalı)
4. **Görseller** (FCOM_IMG, FCTM_IMG) — en son, en pahalı, ayrı onayla, checkpoint temizliği unutulmadan

## Bu runbook'un kapsamadığı
- Script'lerin git'e commit edilmesi (bulgu #1) — ayrı bir karar/adım
- `image_captioning_rag.py`'deki logo-duplication mantığının düzeltilmesi (bulgu #4) — kod değişikliği gerektirir, bu doküman sadece gözlemi kaydeder
- Kök dizindeki `.env.save`, `.envy`, `.envy.save` dosyalarının incelenmesi — untracked ve potansiyel olarak hassas olabilir, bu runbook onlara dokunmadı/içeriklerini okumadı
