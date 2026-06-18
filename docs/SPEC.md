# Paper Lead — Project Spec

## Goal
สร้าง agent ที่ดึง paper AI ล่าสุด → กรอง → สรุป → ส่ง digest ให้ทีมทุกเช้า

## แบ่งงาน

### 🅰️ คน A — Paper Fetcher & Ranker
**ไฟล์:** `src/fetcher.py`, `src/ranker.py`, `src/db.py`

**fetcher.py:**
- `fetch_arxiv(categories, max_results, date_from)` → list[Paper]
- `fetch_huggingface_daily(date)` → list[Paper]
- Paper dataclass: id, title, abstract, authors, url, published_date
- ดึงจาก arXiv API ใช้ library `arxiv`
- ดึงจาก HuggingFace Daily Papers ใช้ requests

**ranker.py:**
- `rank_papers(papers, interests, min_score)` → list[RankedPaper]
- ให้ score 0-1 โดยดูจาก keyword matching กับ interests
- เรียงจาก score สูงสุด
- กรองต่ำกว่า min_score ทิ้ง

**db.py:**
- SQLite schema: papers(id, title, fetched_date, score, digest_date)
- `is_seen(paper_id)` → bool (ไม่ส่งซ้ำ)
- `mark_seen(paper_id, score)` → save

### 🅱️ คน B — Summarizer & Publisher
**ไฟล์:** `src/summarizer.py`, `src/publisher.py`

**summarizer.py:**
- `summarize_batch(papers, llm_config)` → DigestResult
- ส่ง paper abstract ให้ LLM สรุปทีละ batch
- Prompt template ใน `prompts/summarize.md`
- จัดหมวด: Must Read (score > 0.8), Worth Reading (0.5-0.8), Tools & Frameworks
- แต่ละ paper: title, 1-2 บรรทัดสรุป, why it matters

**publisher.py:**
- `publish_digest(digest, config)` → void
- บันทึกเป็น markdown ลง `digest/YYYY-MM-DD.md`
- (Optional) ส่ง Slack/Discord webhook

### ร่วมกัน
**src/main.py:**
```python
# Orchestrator
papers = fetcher.fetch_all(config)
new_papers = db.filter_seen(papers)
ranked = ranker.rank(new_papers, config.interests)
digest = summarizer.summarize(ranked, config.llm)
publisher.publish(digest, config)
db.mark_seen(ranked)
```

## Interface Contract

```json
// RankedPaper (A → B)
{
  "id": "2306.xxxxx",
  "title": "...",
  "abstract": "...",
  "authors": ["..."],
  "url": "https://arxiv.org/abs/...",
  "score": 0.92,
  "topics": ["efficiency", "architecture"],
  "published_date": "2026-06-17"
}

// DigestResult (B → output)
{
  "digest": "# 📰 Paper Lead — 2026-06-18\n...",
  "stats": {
    "total_fetched": 87,
    "total_filtered": 12,
    "must_read": 2,
    "worth_reading": 7,
    "tools": 3
  }
}
```

## Milestone (1 วัน)

| เวลา | คน A | คน B |
|------|-------|-------|
| 09:00 | setup project + arxiv fetcher ได้ | setup project + summarizer skeleton |
| 11:00 | huggingface fetcher + ranker | prompt template + LLM integration |
| 13:00 | db + dedup | publisher (file + optional webhook) |
| 15:00 | main.py orchestrator | รวมกัน + ทดสอบ |
| 16:00 | Demo! | Demo! |
