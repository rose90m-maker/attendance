import json, unicodedata

cache_path = 'chroma_db/.mtime_cache.json'
with open(cache_path, encoding='utf-8') as f:
    cache = json.load(f)

# 모든 xls/xlsx 파일 캐시 삭제 → 개선된 파서로 재인덱싱
removed = []
for k in list(cache.keys()):
    if k.endswith('.xls') or k.endswith('.xlsx') or k.endswith('.XLS'):
        del cache[k]
        removed.append(unicodedata.normalize('NFC', k))

with open(cache_path, 'w', encoding='utf-8') as f:
    json.dump(cache, f, ensure_ascii=False)

print(f"{len(removed)}개 Excel 캐시 삭제 완료 (재인덱싱 대상)")
