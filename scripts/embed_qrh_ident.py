import os, sys, time, uuid, hashlib
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
from qrh_chunker_ident import chunk_qrh, FLEET

load_dotenv()

QDRANT_URL = os.environ['QDRANT_URL']
QDRANT_API_KEY = os.environ['QDRANT_API_KEY']
OPENAI_KEY = os.environ['OPENAI_API_KEY']
PDF_PATH = 'docs/pdfs/QRH.pdf'
COLLECTION = 'documents'
DOC_CODE = 'QRH'

openai_client = OpenAI(api_key=OPENAI_KEY)
qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

print(f'fleet.json entries loaded: {len(FLEET)}')


def extract_text(pdf_path):
  import fitz
  doc = fitz.open(pdf_path)
  text = ''
  for page in doc:
    text += page.get_text()
  doc.close()
  return text.replace('\x00', '')


def embed_texts(texts):
  response = openai_client.embeddings.create(model='text-embedding-3-small', input=texts)
  return [d.embedding for d in response.data]


def delete_existing(doc_code):
  qdrant.delete(
    collection_name=COLLECTION,
    points_selector=Filter(must=[FieldCondition(key='doc_code', match=MatchValue(value=doc_code))])
  )


def chunk_id(doc_code, chunk_index):
  h = hashlib.md5(f'{doc_code}_{chunk_index}'.encode()).hexdigest()
  return str(uuid.UUID(h))


t0 = time.time()
text = extract_text(PDF_PATH)
extract_s = time.time() - t0
print(f'Extracted: {len(text)} chars in {extract_s:.1f}s')

t1 = time.time()
chunks = chunk_qrh(text, doc_code=DOC_CODE)
chunk_s = time.time() - t1
print(f'Chunks: {len(chunks)} in {chunk_s:.1f}s')

sections = {}
kinds = {}
for c in chunks:
  sections[c['section']] = sections.get(c['section'], 0) + 1
  if c['section'] == 'procedure':
    kinds[c['applicability_kind']] = kinds.get(c['applicability_kind'], 0) + 1
print(f'Sections: {sections}')
print(f'Applicability kinds (procedure only): {kinds}')

delete_existing(DOC_CODE)
print(f'Deleted existing: {DOC_CODE}')

upserted = 0
for i in range(0, len(chunks), 50):
  batch = chunks[i:i+50]
  embeddings = embed_texts([c['content'] for c in batch])
  points = [
    PointStruct(
      id=chunk_id(c['doc_code'], c['chunk_index']),
      vector=embeddings[j],
      payload={'doc_code': c['doc_code'], 'chunk_index': c['chunk_index'], 'content': c['content']}
    )
    for j, c in enumerate(batch)
  ]
  qdrant.upsert(collection_name=COLLECTION, points=points)
  upserted += len(points)
  print(f'Progress: {DOC_CODE} {min(i+50, len(chunks))}/{len(chunks)}')

total_s = time.time() - t0
print(f'DONE: {DOC_CODE} {len(chunks)} chunks, {upserted} upserted, total time {total_s:.1f}s')
