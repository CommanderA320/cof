"""
qrh_chunk_fix.py

QRH chunk'larında >10,000 karakter olanları bulur,
~800 karakterlik alt chunk'lara böler, yeniden embed eder,
Qdrant'a yazar ve eski büyük chunk'ı siler.
"""

import os
import time
import hashlib
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct, Filter, FieldCondition, MatchValue
from openai import OpenAI

load_dotenv()

QDRANT_URL     = os.environ['QDRANT_URL']
QDRANT_API_KEY = os.environ['QDRANT_API_KEY']
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']

COLLECTION     = 'documents'
EMBEDDING_MODEL = 'text-embedding-3-small'
CHUNK_SIZE     = 800
CHUNK_OVERLAP  = 100
MAX_CHUNK_SIZE = 10_000  # bu limitin üstündekiler fix edilecek

client  = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
oai     = OpenAI(api_key=OPENAI_API_KEY)


def embed(text: str) -> list[float]:
    r = oai.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return r.data[0].embedding


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Metni overlap'li parçalara böl."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start += size - overlap
    return chunks


def make_id(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**63)


def main():
    print("QRH chunk fix başlıyor...")

    # 1. Tüm QRH chunk'larını scroll et
    print("Qdrant'tan QRH chunk'ları alınıyor...")
    all_points = []
    offset = None

    while True:
        result = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(must=[
                FieldCondition(key='doc_code', match=MatchValue(value='QRH'))
            ]),
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_offset = result
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset
        print(f"  {len(all_points)} chunk alındı...", end='\r')

    print(f"\nToplam QRH chunk: {len(all_points)}")

    # 2. Büyük chunk'ları bul
    big_chunks = [p for p in all_points if len(p.payload.get('content', '')) > MAX_CHUNK_SIZE]
    print(f"Büyük chunk (>{MAX_CHUNK_SIZE} karakter): {len(big_chunks)}")

    if not big_chunks:
        print("Düzeltilecek chunk yok. Çıkılıyor.")
        return

    fixed = 0
    skipped = 0

    for point in big_chunks:
        payload = point.payload
        content = payload.get('content', '')
        doc_code = payload.get('doc_code', 'QRH')
        chunk_index = payload.get('chunk_index', 0)

        print(f"\n[fix] chunk_index={chunk_index}, boyut={len(content)} karakter")

        # Alt chunk'lara böl
        sub_texts = split_text(content)
        print(f"  → {len(sub_texts)} alt chunk")

        new_ids = []
        for i, sub in enumerate(sub_texts):
            key = f"QRH_fix_{chunk_index}_{i}"
            uid = make_id(key)
            new_ids.append(uid)

            try:
                vec = embed(sub)
            except Exception as e:
                print(f"  [warn] Embed hatası: {e}")
                time.sleep(3)
                try:
                    vec = embed(sub)
                except Exception as e2:
                    print(f"  [error] Embed başarısız: {e2}, skip")
                    skipped += 1
                    continue

            new_payload = {**payload, 'content': sub, 'chunk_index': f"{chunk_index}_{i}"}

            try:
                client.upsert(
                    collection_name=COLLECTION,
                    points=[PointStruct(id=uid, vector=vec, payload=new_payload)]
                )
            except Exception as e:
                print(f"  [warn] Upsert hatası: {e}")
                skipped += 1
                continue

            time.sleep(0.05)

        # Eski büyük chunk'ı sil
        try:
            client.delete(
                collection_name=COLLECTION,
                points_selector=[point.id]
            )
            print(f"  [ok] Eski chunk silindi, {len(sub_texts)} yeni chunk yazıldı")
            fixed += 1
        except Exception as e:
            print(f"  [warn] Silme hatası: {e}")

    print(f"\n✅ Tamamlandı: {fixed} chunk fix edildi, {skipped} skip")


if __name__ == '__main__':
    main()
