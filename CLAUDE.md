# COF — Commander's Operational Framework
## Claude Code Project Instructions

## Proje Özeti
COF, Turkish Airlines A319/A320/A321 için AI destekli uçuş operasyonları karar destek sistemi.
Tek dosya PWA (index.html). GitHub Pages'te deploy edilir.

## Mimari
- **Frontend:** index.html (~4200 satır) — github.com/CommanderA320/cof → commandera320.github.io/cof
- **Backend:** api/search.js — github.com/CommanderA320/cofbackend → cofbackend.vercel.app
- **Vector DB:** Qdrant cloud — d537c7d4-c6f3-4aee-bb6b-acfb66fd2d3c.europe-west3-0.gcp.cloud.qdrant.io
- **Collection:** documents | payload field: doc_code
- **AI Model:** claude-sonnet-4-6 (Anthropic API)
- **Embeddings:** OpenAI text-embedding-3-small

## Deploy Workflow
### Frontend (index.html):
```bash
cd ~/Documents/GitHub/cof
cp ~/Downloads/index.html .
git add index.html && git commit -m "message" && GIT_LFS_SKIP_SMUDGE=1 git push
```
### Backend (search.js):
```bash
cd ~/Documents/GitHub/cofbackend
cp ~/Downloads/search.js api/search.js
git add api/search.js && git commit -m "message" && git push
```
### Conflict durumunda:
```bash
git rebase --abort
git fetch origin && git reset --hard origin/main
```

## Qdrant Inventory (Mayıs 2026)
- QRH×32,549 · FCOM×12,959 · MEL×2,593 · EASA_Easy_Access_Rules×2,200 · AFM×1,954
- FCTM_IMG×668 · FCOM_IMG×10,744 (v1 — THY logo, v2 script çalışıyor)
- Toplam: ~70,000+ chunk

## SOURCE_LIMITS (search.js)
FCOM×12, QRH×10, MEL×10, FCTM×6, AFM×4, OM_Part-A×5, OM_Part-B×3, CCM×5, GOM×4,
ICAO_4444×3, ICAO_8168×3, ICAO_Annex_2×3, ICAO_8168-IIII×2, ICAO_Annex_6-1×3,
EASA_Easy_Access_Rules×5, Dispatch_Manual×3, FCOM_IMG×3, FCTM_IMG×2

## Kritik Geliştirme Kuralları
- **HTML editing:** grep ile satır numarası bul, doğrudan hedefle. Python regex ile HTML manipülasyonu YASAK.
- **JS syntax kontrolü:** node --check index.html (veya /tmp/test.js)
- **AI_SYSTEM:** Asla compress etme. Byte count değişiklik öncesi/sonrası doğrula.
- **Qdrant:** doc_code field'ı kullan (source değil)
- **Tüm kaynaklar her sorguda aranır** — dynamic filtering yok

## Bilinen Sorunlar
- FCOM_IMG v1 chunk'ları THY logosu içeriyor — v2 script (image_captioning_rag.py) çalışıyor, bitince silinecek
- QRH 38k chunk sorunu — düzeltme bekliyor
- LVO corrupt chunks (×9) — remediation bekliyor
- OM Part-A blank chunks (×1,207) — silinecek

## Domain Kuralları (Bahadır ground truth)
- FCOM PRO-ABN hem abnormal hem emergency içerir — ayrım YOK
- AFM checklist adımı DEĞİL — conflict resolution otoritesidir
- Hem RED hem WHITE OEB, ECAM ENTRY eşleşince ECAM'ı override eder
- OEB62: her iki bleed <60 PSI → ECAM PROC; en az biri ≥60 PSI → OEB prosedürü
