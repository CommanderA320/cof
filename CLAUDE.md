# COF — Commander's Operational Framework
## Claude Code Project Instructions

## Proje Özeti
COF, Turkish Airlines A319/A320/A321 için AI destekli uçuş operasyonları karar destek sistemi.
Tek dosya PWA (index.html). GitHub Pages'te deploy edilir.

## Mimari
- **Frontend:** index.html (~4200 satır) — github.com/CommanderA320/cof → commandera320.github.io/cof
- **Backend — iki ayrı endpoint (aynı repo, farklı görevler):**
  - **api/search.js:** Qdrant + OpenAI embedding üzerinden doküman arama. Hiçbir Claude/Anthropic
    çağrısı YOK.
  - **api/query.js:** Asıl FOR-DEC rapor üretim motoru. Server-side ANTHROPIC_API_KEY ile
    api.anthropic.com/v1/messages'a proxy. Frontend model adını açıkça gönderiyor
    (varsayılan claude-sonnet-4-6, kullanıcı UI rozetiyle claude-opus-4-6'ya geçebilir).
  - → cofbackend.vercel.app (her ikisi de aynı Vercel deploy'unda)
- **Vector DB:** Qdrant cloud, cluster "cof2" (free tier) — https://bc42f0d3-c798-4e1a-9a66-a3b45674a4f7.europe-west3-0.gcp.cloud.qdrant.io
  (Not: eski cluster "cof1" Eylül 2026'da disk krizi sonrası silindi, veriler buraya taşındı)
- **Collection:** documents | payload field: doc_code
- **FOR-DEC AI Model:** claude-sonnet-4-6 (varsayılan) / claude-opus-4-6 (kullanıcı seçimi) —
  Sonnet 5'e yükseltme henüz yapılmadı, bekleyen bir karar (image_captioning_rag.py script'i
  Eylül 2026'da sonnet-5'e geçirildi ama bu SADECE görsel captioning için, FOR-DEC motorunu
  etkilemez)
- **Embeddings:** OpenAI text-embedding-3-small
- **Ölü kod notu:** index.html'de `thy_api_key` (localStorage) tanımlı ve okunuyor ama hiçbir
  fetch çağrısında kullanılmıyor — muhtemelen eski, artık geçersiz bir BYOK mimarisinden kalıntı

## Deploy Workflow
### Frontend (index.html) — dokümante edilen, hâlâ geçerli akış:
cd ~/Developer/GitHub/cof
cp ~/Downloads/index.html .
git add index.html && git commit -m "message" && GIT_LFS_SKIP_SMUDGE=1 git push
(Not: cof/cofbackend repoları Eylül 2026'da ~/Developer/GitHub/'a taşındı, iCloud senkron
sorunları yüzünden — ama index.html'in kendisi bu taşımadan sonra hiç düzenlenmedi, bu yüzden
Downloads-kopyalama adımının hâlâ geçerli olup olmadığı doğrulanmadı. Script/config dosyaları
artık repo içinde doğrudan düzenleniyor.)
### Backend (search.js / query.js):
cd ~/Developer/GitHub/cofbackend
git add api/*.js && git commit -m "message" && git push
### Conflict durumunda:
git rebase --abort
git fetch origin && git reset --hard origin/main

## Qdrant Inventory (Eylül 2026, migration + revizyon sonrası)
FCOM×12,959 · QRH×834 (THY tail-based format, eski Airbus master'dan değişti)
MEL×2,569 · AFM×1,978 · FCTM×657 · OM_Part-A×1,213 · OM_Part-B×205
CCM×864 · GOM×1,304 · EASA_Easy_Access_Rules×710
Dispatch_Manual×199 · LVO×8 · AML×63 · AML_Pages×26 · De-Icing×464
DGR_Allowed×42 · DGR_Guide×7 · SHT_OPS×760
ICAO_4444×983 · ICAO_8168×243 · ICAO_8168-IIII×156 · ICAO_Annex_2×65 · ICAO_Annex_6-1×184
FCOM_IMG×9,097 (v2, logo duplikatları temizlendi)
FCTM_IMG×489 (v2, tamamen yeniden captioning yapıldı, v1 kalıntıları temizlendi)

## SOURCE_LIMITS (search.js)
FCOM×12, QRH×10, MEL×10, FCTM×6, AFM×4, OM_Part-A×5, OM_Part-B×3, CCM×5, GOM×4,
ICAO_4444×3, ICAO_8168×3, ICAO_Annex_2×3, ICAO_8168-IIII×2, ICAO_Annex_6-1×3,
EASA_Easy_Access_Rules×5, Dispatch_Manual×3, FCOM_IMG×3, FCTM_IMG×2,
AML×3, De-Icing×3, AML_Pages×3, DGR_Allowed×3, DGR_Guide×3, SHT_OPS×3

## Doküman Revizyon İş Akışı
Yeni/revize PDF gelince (docs/pdfs/{doc_code}.pdf, dosya adı = doc_code, case-sensitive):
1. python3 scripts/embed_doc.py {doc_code}  (QRH için: qrh_chunker_ident.py + embed_qrh_ident.py)
2. Qdrant count() ile doğrula
3. Canlı /api/search testi ile içerik doğrula
4. doc-verifier subagent'ı (cofbackend/.claude/agents/) bu 2-3 adımı otomatik yapabilir
Yeni bir doc_code (yeni doküman türü) eklendiyse SOURCE_LIMITS'e de eklenmeli, yoksa
embed olur ama aranamaz.

## CI Durumu (embed.yml)
Push tetikleyicisi Eylül 2026'da DEVRE DIŞI bırakıldı (sadece workflow_dispatch).
Sebep: lfs:false yüzünden çalışmıyordu VE gömülü chunking mantığı güncel değil
(qrh_chunker_ident.py'yi, Dispatch_Manual özel işlemesini, görsel captioning'i
yansıtmıyor). Yeniden açmak ayrı, kapsamlı bir iş — şimdilik tüm embed işlemleri elle.

## Kritik Geliştirme Kuralları
- **HTML editing:** grep ile satır numarası bul, doğrudan hedefle. Python regex ile HTML
  manipülasyonu YASAK.
- **JS syntax kontrolü:** node --check index.html (veya /tmp/test.js)
- **AI_SYSTEM:** Asla compress etme. Byte count değişiklik öncesi/sonrası doğrula.
- **Qdrant:** doc_code field'ı kullan (source değil)
- **search.js vs query.js:** Arama/filtreleme mantığı değişiklikleri search.js'te;
  FOR-DEC prompt/model değişiklikleri query.js'te — ikisini karıştırma.
- **Yeni script dosyası = sor, sessizce bırakma:** Her yeni Python/JS script dosyası
  oluşturulunca, iş bitmeden "bu dosyayı commit+push etmemi ister misin" diye sorulmalı.
- **Destructive işlemler (silme, config değişikliği):** Önce kapsamı göster (hangi
  doc_code, kaç nokta), sonra onay al. Config denemelerinde ikiden fazla kör tahmin yapma
  — durup dokümantasyona bak ya da destek talebi aç.
- **Güvenlik:** API key'ler asla frontend'e (index.html) veya git geçmişine gömülmez,
  sadece .env / Vercel env üzerinden okunur (Eylül 2026'da denetlendi, temiz).

## Bilinen Sorunlar (öncelik sırasıyla)
1. CI otomasyonu kapsamlı yeniden yazım bekliyor (yukarıya bakın)
2. MEL applicability inheritance zayıf — tail-list boilerplate arama skorunu bozuyor;
   ata_ref payload alanı hiç doldurulmamış (0/36.079 nokta) — planlı çözüm:
   applicability'yi ayrı payload alanına taşımak
3. QRH/FCOM'da 4 tescil (TC-LBS, TC-LTZ, TC-LUA, TC-NDF) senkron değil — kabul edilebilir,
   iki kaynak dokümanın revizyon farkı
4. FOR-DEC motorunda (query.js) hâlâ sonnet-4-6 — sonnet-5'e geçiş bekleyen bir karar
5. index.html'de ölü kod: thy_api_key tanımlı ama kullanılmıyor — temizlenebilir
6. index.html'de hem client-side hem (artık) server-side logo/duplicate-image filtresi
   var — iki katmanın ikisinin de gerekip gerekmediği incelenmedi

## Domain Kuralları (Bahadır'ın düzeltmeleri — ground truth)
- FCOM PRO-ABN hem abnormal hem emergency prosedürlerini içerir — ayrım YOK
- AFM checklist adımı DEĞİLDİR — conflict resolution otoritesidir
- ECAM ADV conditions ayrı monitoring kanalıdır — OEB'de aranmaz
- Hem RED hem WHITE OEB, ECAM ENTRY field eşleşince ECAM'ı override eder
- OEB62 conditional: her iki bleed <60 PSI → ECAM PROC; en az biri ≥60 PSI → OEB prosedürü
