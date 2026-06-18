# 📰 Paper Lead — AI Research Digest Agent

Agent ที่อ่าน paper AI ล่าสุดให้ทุกวัน → สรุป → ส่ง digest ให้ทีม เหมือนมี R&D Lead คนนึง

## ทำไมต้องมี

- AI เปลี่ยนเร็ว — paper ใหม่ออกวันละ 100+ ฉบับ ไม่มีใครอ่านทัน
- ทีมตามไม่ทัน → พลาด breakthrough
- Paper Lead = ตื่นมาเห็นสรุป 5 นาที รู้ว่าวงการไปถึงไหนแล้ว

## Architecture

```
arXiv API ──┐
             ├──▶ Fetcher ──▶ Ranker ──▶ Summarizer ──▶ Publisher ──▶ Digest
HF Daily ───┘     (คน A)      (คน A)      (คน B)        (คน B)
```

## แบ่งงาน 2 คน

| 🅰️ คน A — Paper Fetcher & Ranker | 🅱️ คน B — Summarizer & Publisher |
|---|---|
| ดึง paper จาก arXiv API + HuggingFace Daily Papers | สรุป paper ด้วย LLM |
| กรอง + ให้ relevance score ตาม interest profile | จัดหมวดหมู่: Must Read / Worth Reading / Tools |
| เก็บ metadata ลง SQLite (ไม่ส่งซ้ำ) | สร้าง digest markdown |
| `src/fetcher.py`, `src/ranker.py` | `src/summarizer.py`, `src/publisher.py` |

## Interface (JSON schema ที่ตกลงกัน)

```json
// A ส่งให้ B:
{
  "date": "2026-06-18",
  "papers": [
    {
      "id": "2306.xxxxx",
      "title": "Mamba-3: Linear Attention at Scale",
      "abstract": "...",
      "authors": ["..."],
      "url": "https://arxiv.org/abs/2306.xxxxx",
      "score": 0.92,
      "topics": ["architecture", "efficiency"]
    }
  ]
}

// B ตอบ — digest markdown:
{
  "digest": "# 📰 Paper Lead — 18 Jun 2026\n...",
  "stats": {
    "total_fetched": 87,
    "total_filtered": 12,
    "must_read": 2,
    "worth_reading": 7,
    "tools": 3
  }
}
```

## Quick Start

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml  # แก้ topics ที่สนใจ
python src/main.py
```

## Config

ดู `config.example.yaml`

## Output

Digest ออกมาเป็น markdown ใน `digest/YYYY-MM-DD.md`

## License

MIT
