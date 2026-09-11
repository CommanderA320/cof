import os, sys, re, fitz, uuid, hashlib
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, PayloadSchemaType

if len(sys.argv) != 2:
  print('Usage: python3 scripts/embed_doc.py <doc_code>')
  sys.exit(1)

load_dotenv()

QDRANT_URL = os.environ['QDRANT_URL']
QDRANT_API_KEY = os.environ['QDRANT_API_KEY']
OPENAI_KEY = os.environ['OPENAI_API_KEY']
SPECIFIC_DOC = sys.argv[1].strip()
PDF_DIR = 'docs/pdfs'
COLLECTION = 'documents'

openai_client = OpenAI(api_key=OPENAI_KEY)
qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

try:
  qdrant.get_collection(COLLECTION)
  print(f'Collection {COLLECTION} exists')
except Exception:
  qdrant.create_collection(
    collection_name=COLLECTION,
    vectors_config=VectorParams(size=1536, distance=Distance.COSINE)
  )
  print(f'Created collection {COLLECTION}')
  qdrant.create_payload_index(
    collection_name=COLLECTION,
    field_name='doc_code',
    field_schema=PayloadSchemaType.KEYWORD
  )
  print('Created doc_code index')

try:
  qdrant.create_payload_index(
    collection_name=COLLECTION,
    field_name='doc_code',
    field_schema=PayloadSchemaType.KEYWORD
  )
except Exception as e:
  print(f'Index note: {e}')

def get_pdfs():
  if not os.path.exists(PDF_DIR):
    print('No docs/pdfs folder')
    return []
  files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith('.pdf')]
  if SPECIFIC_DOC:
    files = [f for f in files if os.path.splitext(f)[0].upper() == SPECIFIC_DOC.upper()]
  return files

def extract_text(pdf_path):
  try:
    doc = fitz.open(pdf_path)
    text = ''
    for page in doc:
      text += page.get_text()
    doc.close()
    return text.replace('\x00', '')
  except Exception as e:
    print(f'WARNING: Could not extract {pdf_path}: {e}')
    return ''

def chunk_text(text, doc_code):
  dc = doc_code.upper()

  if dc == 'QRH':
    chunks = []
    # QRH sayfa bazlı chunking - her prosedür başlığında böl
    pages = re.split(r'\n(?=[A-Z][A-Z /\-]{3,50}\n)', text)
    for idx, page in enumerate(pages):
      page = page.strip()
      if len(page) > 100:
        chunks.append({'doc_code': doc_code, 'chunk_index': idx, 'content': page})
    if chunks:
      print(f'QRH chunking: {len(chunks)} chunks')
      return chunks
    print('QRH fallback to sliding window')

  if dc == 'FCOM':
    chunks = []
    # FCOM bölüm bazlı chunking - her prosedür/sistem bölümünde böl
    sections = re.split(r'\n(?=(?:PRO|SYS|DSC|LIM|ABN|NOR|SUP|FLT|ENG|HYD|ELEC|FUEL|PRES|NAV|COM|APU|DOOR|LAND|FLAP)[- ][A-Z0-9])', text)
    for idx, section in enumerate(sections):
      section = section.strip()
      # Uzun bölümleri 1500 karakter ile böl
      if len(section) > 1500:
        for i in range(0, len(section), 1300):
          sub = section[i:i+1500].strip()
          if len(sub) > 100:
            chunks.append({'doc_code': doc_code, 'chunk_index': len(chunks), 'content': sub})
      elif len(section) > 100:
        chunks.append({'doc_code': doc_code, 'chunk_index': len(chunks), 'content': section})
    if chunks:
      print(f'FCOM section-based chunking: {len(chunks)} chunks')
      return chunks
    print('FCOM fallback to sliding window')

  chunk_size = 2000 if 'EASA' in doc_code else 1500
  overlap = 200
  chunks = []
  start = 0
  index = 0
  while start < len(text):
    end = min(start + chunk_size, len(text))
    content = text[start:end].strip()
    if len(content) > 50:
      chunks.append({'doc_code': doc_code, 'chunk_index': index, 'content': content})
      index += 1
    start += chunk_size - overlap
  return chunks

def embed_texts(texts):
  response = openai_client.embeddings.create(
    model='text-embedding-3-small',
    input=texts
  )
  return [d.embedding for d in response.data]

def delete_existing(doc_code):
  try:
    qdrant.delete(
      collection_name=COLLECTION,
      points_selector=Filter(
        must=[FieldCondition(key='doc_code', match=MatchValue(value=doc_code))]
      )
    )
  except Exception as e:
    print(f'Delete note: {e}')

def chunk_id(doc_code, chunk_index):
  h = hashlib.md5(f'{doc_code}_{chunk_index}'.encode()).hexdigest()
  return str(uuid.UUID(h))

pdfs = get_pdfs()
if not pdfs:
  print('No PDFs to process')
  sys.exit(0)

for pdf_file in pdfs:
  doc_code = os.path.splitext(pdf_file)[0]
  pdf_path = os.path.join(PDF_DIR, pdf_file)

  print(f'Processing: {pdf_file}')
  text = extract_text(pdf_path)
  print(f'Extracted: {len(text)} chars')

  if not text:
    print(f'SKIP: {doc_code} (no text extracted)')
    continue
  chunks = chunk_text(text, doc_code)
  print(f'Chunks: {len(chunks)}')

  delete_existing(doc_code)
  print(f'Deleted existing: {doc_code}')

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
    print(f'Progress: {doc_code} {min(i+50, len(chunks))}/{len(chunks)}')

  print(f'DONE: {doc_code} {len(chunks)} chunks')

print('ALL DONE')
