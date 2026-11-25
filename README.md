# Ai - עוזר קולי אישי

שלב 1: כלים ומוח (טקסט בלבד). הקול יתווסף אחרי בדיקת ספייקים.

## דרישות
Python 3.11+ (ללא תלויות חיצוניות) ו-Claude Code מותקן ומחובר (`claude` ואז `/login`).

## הרצה
```bash
cp config.example.json config.json
python -m unittest discover -s tests -v
python -m ai tools
python -m ai call calculator '{"expression": "12*(3+4)"}'
python -m ai chat
```

## Home Assistant
```bash
export HA_TOKEN="<long-lived token>"
```
הטוקן נוצר בפרופיל המשתמש ב-Home Assistant. אל תשמור אותו בקובץ.

## מבנה
- `ai/tools.py` - כלים: מחשבון, שעה, טיימר, מזג אוויר, Home Assistant, העדפות.
- `ai/mcp_server.py` - חושף את הכלים ל-`claude -p`.
- `ai/brain.py` - עוטף את `claude -p`.
- `docs/decisions.md` - החלטות וסיכונים פתוחים.
- `docs/HARDWARE.md` - חומרה, הרכבה, בדיקות קבלה, בטיחות.
- `docs/HANDOFF.md` - מסמך העברה מלא להמשך עבודה בצ'אט אחר.
