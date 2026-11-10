#!/usr/bin/env python3
"""任天堂歷史 LLM Wiki — 自動檢查。

    python3 tools/check.py wiki      # Lint：檢查 Wiki 的出處、連結、目錄、爭議說法、數字對照（W1–W6）
    python3 tools/check.py outline   # 檢查大綱的年份是否都在引用的 Wiki 頁（O1）
    python3 tools/check.py slides    # 檢查簡報與 Wiki 是否對得上（D1–D5）
    python3 tools/check.py all       # 兩個都跑

只用 Python 標準函式庫。有任何一項沒過，會列出原因並以非零碼結束。
規則說明見 CLAUDE.md；不要為了讓檢查通過而改這支程式。
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
WIKI = ROOT / "wiki"
DECK = ROOT / "docs" / "index.html"

CITE_RE = re.compile(r"\[S(\d+)\]")
YEAR_RE = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\d])")
FACT_HINT_RE = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)|\d+(?:\.\d+)?\s*(?:億|萬|百萬|%|台|套|美元|款|歲|年)")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
LINKTEXT_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
MYTH_WORDS = ["情趣旅館", "love hotel"]

# 簡報裡第一次出現時必須用 <dfn> 解釋的專有名詞
TERMS = ["花札", "紅白機", "街機", "第三方", "品質認證", "枯れた技術の水平思考", "掌機"]
MAX_SLIDE_CHARS = 200
MIN_SLIDES, MAX_SLIDES = 8, 12


# ───────────────────────── 共用 ─────────────────────────

def source_ids() -> set[str]:
    return {m.group(1) for p in RAW.glob("s*-*.md") if (m := re.match(r"s(\d+)-", p.name))}


def raw_texts() -> dict[str, str]:
    out = {}
    for p in RAW.glob("s*-*.md"):
        m = re.match(r"s(\d+)-", p.name)
        if m:
            out[m.group(1)] = p.read_text(encoding="utf-8")
    return out


EQ_RE = re.compile(r"(\d+)\s*[−-]\s*(\d+)\s*=\s*(\d+)")


def wiki_pages() -> list[Path]:
    return sorted(p for p in WIKI.rglob("*.md"))


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def report(title: str, errors: list[str]) -> bool:
    if errors:
        print(f"❌ {title}：{len(errors)} 項沒過")
        for e in errors:
            print("   -", e)
        return False
    print(f"✅ {title}：全部通過")
    return True


# ───────────────────────── Wiki Lint ─────────────────────────

def check_wiki() -> bool:
    ids = source_ids()
    raws = raw_texts()
    pages = wiki_pages()
    errors: list[str] = []

    index = WIKI / "index.md"
    index_links: set[Path] = set()
    if index.exists():
        for target in LINK_RE.findall(index.read_text(encoding="utf-8")):
            if not target.startswith("http"):
                index_links.add((index.parent / target.split("#")[0]).resolve())
    else:
        errors.append("W4 找不到 wiki/index.md")

    for page in pages:
        name = rel(page)
        text = page.read_text(encoding="utf-8")
        is_meta = page.name in ("index.md", "log.md")
        in_myths = "myths" in page.parts

        for n, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#") or set(s) <= set("|-: "):
                continue
            # W1：含年份或數字的事實句要有出處（目錄與紀錄頁除外）
            # 連結文字是頁面標題、不是事實，先拿掉再判斷（第 1 次執行時的誤判，見 log.md）
            plain = LINKTEXT_RE.sub("", s)
            if not is_meta and FACT_HINT_RE.search(plain) and not CITE_RE.search(s):
                errors.append(f"W1 {name}:{n} 有年份或數字但沒標出處 → {s[:50]}")
            # W2：出處要對得到摘錄卡
            for sid in CITE_RE.findall(s):
                if sid not in ids:
                    errors.append(f"W2 {name}:{n} 引用 [S{sid}]，但 raw/ 沒有 s{sid}-*.md")
            # W6：句子裡的年份與數字，要真的出現在它引用的摘錄卡裡（第 1 次執行後新增，見 log.md）
            cites = set(CITE_RE.findall(s))
            if cites and not is_meta:
                src = "".join(raws.get(c, "") for c in cites)
                body = LINKTEXT_RE.sub("", CITE_RE.sub("", s))
                derived = set()
                for x, y, z in EQ_RE.findall(body):           # 本 Wiki 自己算的數字：驗算正確就放行
                    if int(x) - int(y) == int(z):
                        derived.add(z)
                    else:
                        errors.append(f"W6 {name}:{n} 算式錯誤：{x} − {y} ≠ {z}")
                for tok in sorted(set(NUM_RE.findall(body))):
                    if len(tok.replace(",", "").replace(".", "")) < 2 or tok in derived:
                        continue
                    if tok not in src:
                        errors.append(f"W6 {name}:{n} 的「{tok}」在引用的摘錄卡（{'、'.join('S'+c for c in sorted(cites))}）裡找不到")
            # W5：查無證據的說法只能出現在 myths/，或明講查無證據
            if not is_meta and not in_myths and any(w in s for w in MYTH_WORDS):
                if "查無證據" not in s and "myths/" not in s:
                    errors.append(f"W5 {name}:{n} 提到未證實的說法，卻沒有標明「查無證據」→ {s[:50]}")

        # W3：連結要存在
        for target in LINK_RE.findall(text):
            if target.startswith("http"):
                continue
            dest = (page.parent / target.split("#")[0]).resolve()
            if not dest.exists():
                errors.append(f"W3 {name} 連到不存在的頁面：{target}")

        # W4：每一頁都要列在目錄
        if not is_meta and page.resolve() not in index_links:
            errors.append(f"W4 {name} 沒有列在 wiki/index.md")

    print(f"（檢查 {len(pages)} 個 Wiki 頁面，{len(ids)} 個來源）")
    return report("Wiki 檢查", errors)


# ───────────────────────── 簡報檢查 ─────────────────────────

SLIDE_RE = re.compile(r'<section class="slide"([^>]*)>(.*?)</section>', re.S)
WIKI_ATTR_RE = re.compile(r'data-wiki="([^"]*)"')
FOOTER_RE = re.compile(r'<footer class="src">.*?</footer>', re.S)
NOTES_RE = re.compile(r'<aside class="notes">.*?</aside>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def visible_text(body: str) -> str:
    body = FOOTER_RE.sub(" ", body)
    body = NOTES_RE.sub(" ", body)
    return html.unescape(TAG_RE.sub(" ", body))


def check_slides() -> bool:
    if not DECK.exists():
        return report("簡報檢查", ["找不到 docs/index.html"])
    deck = DECK.read_text(encoding="utf-8")
    slides = SLIDE_RE.findall(deck)
    errors: list[str] = []

    # D3：張數、學習目標、回顧
    if not (MIN_SLIDES <= len(slides) <= MAX_SLIDES):
        errors.append(f"D3 共 {len(slides)} 張，應該在 {MIN_SLIDES}～{MAX_SLIDES} 張之間")
    if slides and "學習目標" not in visible_text(slides[0][1]):
        errors.append("D3 第 1 張沒有「學習目標」")
    if slides and "回顧" not in visible_text(slides[-1][1]):
        errors.append(f"D3 最後一張（第 {len(slides)} 張）沒有「回顧」")

    first_seen: dict[str, int] = {}
    for i, (attrs, body) in enumerate(slides, 1):
        text = visible_text(body)

        # D1：出處
        m = WIKI_ATTR_RE.search(attrs)
        cited = m.group(1).split() if m else []
        if not cited:
            errors.append(f"D1 第 {i} 張沒有標 Wiki 出處（data-wiki）")
        cited_text = ""
        for c in cited:
            p = WIKI / c
            if not p.exists():
                errors.append(f"D1 第 {i} 張引用的 wiki/{c} 不存在")
            else:
                cited_text += p.read_text(encoding="utf-8")

        # D2：年份與數字都要在引用的 Wiki 頁出現
        if cited_text:
            for tok in sorted(set(NUM_RE.findall(text))):
                if len(tok.replace(",", "").replace(".", "")) < 2:
                    continue  # 個位數（例如「第 3 代」）不檢查
                if tok not in cited_text:
                    errors.append(f"D2 第 {i} 張的「{tok}」在引用的 Wiki 頁裡找不到（{', '.join(cited)}）")

        # D4：專有名詞第一次出現要有 <dfn>
        for term in TERMS:
            if term in text and term not in first_seen:
                first_seen[term] = i
                if not re.search(rf"<dfn>[^<]*{re.escape(term)}[^<]*</dfn>", body):
                    errors.append(f"D4 第 {i} 張第一次出現「{term}」，但沒有用 <dfn> 解釋")

        # D5：字數
        chars = len(re.sub(r"\s", "", text))
        if chars > MAX_SLIDE_CHARS:
            errors.append(f"D5 第 {i} 張正文 {chars} 字，超過 {MAX_SLIDE_CHARS} 字")

    print(f"（檢查 {len(slides)} 張投影片）")
    return report("簡報檢查", errors)


# ───────────────────────── 大綱檢查（第 1 次執行後新增，見 log.md）─────────────────────────

OUTLINE = ROOT / "slides" / "outline.md"


def check_outline() -> bool:
    """O1：大綱每一列「要點」裡的年份，都要出現在該列引用的 Wiki 頁。
    在寫 HTML 之前就抓出「引用頁漏列」，不用等到投影片做完才發現。"""
    if not OUTLINE.exists():
        return report("大綱檢查", ["找不到 slides/outline.md"])
    errors: list[str] = []
    rows = [l for l in OUTLINE.read_text(encoding="utf-8").splitlines() if re.match(r"^\|\s*\d+\s*\|", l)]
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        num, title, points, cited = cells[0], cells[1], cells[2], cells[3]
        pages = [c.strip() for c in re.split(r"[、,\s]+", cited) if c.strip()]
        text = ""
        for pg in pages:
            f = WIKI / pg
            if not f.exists():
                errors.append(f"O1 第 {num} 張引用的 wiki/{pg} 不存在")
            else:
                text += f.read_text(encoding="utf-8")
        for y in sorted(set(YEAR_RE.findall(title + " " + points))):
            if y not in text:
                errors.append(f"O1 第 {num} 張的「{y}」不在引用的 Wiki 頁裡，請補引用頁或改要點")
    print(f"（檢查大綱 {len(rows)} 列）")
    return report("大綱檢查", errors)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    ok = True
    if mode in ("wiki", "all"):
        ok &= check_wiki()
    if mode in ("outline", "all"):
        ok &= check_outline()
    if mode in ("slides", "all"):
        ok &= check_slides()
    if mode not in ("wiki", "outline", "slides", "all"):
        print(__doc__)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
