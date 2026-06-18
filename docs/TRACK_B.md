# Track B — Summarizer & Publisher

## สถานะ: ✅ Ready for testing

F เอา Track B — สรุป paper + publish digest

### ไฟล์ที่เขียนแล้ว

| ไฟล์ | หน้าที่ | สถานะ |
|------|---------|--------|
| `src/summarizer.py` | สรุป paper ด้วย LLM, จัดหมวดหมู่, fallback manual | ✅ |
| `src/publisher.py` | บันทึกไฟล์ + Slack/Discord webhook | ✅ |
| `src/main.py` | Orchestrator เชื่อม A + B | ✅ |
| `prompts/summarize.md` | Prompt template สรุป | ✅ |

### วิธีทดสอบ

```bash
# ติดตั้ง dependencies
pip install -r requirements.txt

# ตั้ง API key
export SIAM_AI_API_KEY=sk-xxx  # หรือ OPENAI_API_KEY ถ้าใช้ provider อื่น

# Dry run (ไม่บันทึกไฟล์)
cd src
python main.py --dry-run

# รันจริง (บันทึกลง digest/)
python main.py
```

### ถ้า Track A ยังไม่เสร็จ

`main.py` จะใช้ sample data อัตโนมัติ — รันทดสอบได้เลย

### ถ้าไม่มี OPENAI_API_KEY

`summarizer.py` จะ fallback เป็น manual digest (ไม่ใช้ LLM) — ยังได้ output อยู่

---

### TODO (F ทำต่อได้)

- [ ] ปรับ prompt template ใน `prompts/summarize.md` ให้เข้าทีม
- [ ] เพิ่ม Thai-specific instructions
- [ ] ทดสอบกับ OpenAI API จริง
- [ ] ตั้ง Slack/Discord webhook ใน config.yaml
- [ ] ปรับ categorize logic ถ้าอยากเปลี่ยน threshold
