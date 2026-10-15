#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四级大纲词库构建脚本（数据清洗 / 校验 / 合并）

数据来源（均为国内权威教育机构开源的公开词库）：
  1) 大学英语四级大纲词汇表（词头 + 音标 + 中文释义）
     https://github.com/mahavivo/english-wordlists  CET4_edited.txt
     原始出处：全国大学英语四、六级考试大纲（2016 年版）词表 / 贵州大学外国语学院整理
  2) 有道考神四级词库（音标 + 释义 + 真题例句）
     https://github.com/kajweb/dict   book/CET4_1.zip、CET4_2.zip、CET4_3.zip

处理流程：
  下载 → 解析大纲词表 → 解析有道词库 → 逐词合并 → 清洗校验 → 输出 data/words.json
输出：
  data/words.json  （唯一词库文件，供网页与其它程序读取）
  data/words.js    （同名数据的 JS 包装，便于 file:// 直接打开网页时离线加载）
"""

import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_DIR = os.path.join(ROOT, ".cache")

OUTLINE_URL = "https://raw.githubusercontent.com/mahavivo/english-wordlists/master/CET4_edited.txt"
YOUDao_URLS = [
    "https://raw.githubusercontent.com/kajweb/dict/master/book/1521164649209_CET4_1.zip",
    "https://raw.githubusercontent.com/kajweb/dict/master/book/1521164635506_CET4_2.zip",
    "https://raw.githubusercontent.com/kajweb/dict/master/book/1521164643060_CET4_3.zip",
]
# 补充词库：仅用于补齐缺失的词典例句（无真题例句字段）
YOUDao_EXTRA_URLS = [
    "https://raw.githubusercontent.com/kajweb/dict/master/book/1521164653685_Level4_2.zip",
    "https://raw.githubusercontent.com/kajweb/dict/master/book/1521164625401_Level4luan_2.zip",
]

# 有道数据中混入的少量法语字母，统一转写为 ASCII
FR_EN = [["é", "e"], ["ê", "e"], ["è", "e"], ["ë", "e"], ["à", "a"], ["â", "a"],
         ["ç", "c"], ["î", "i"], ["ï", "i"], ["ô", "o"], ["ù", "u"], ["û", "u"],
         ["ü", "u"], ["ÿ", "y"]]

POS_RE = re.compile(r"^(?:[a-z]{1,4}\.|prep|conj|art|pron|num|int|aux)\.?", re.I)


def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch(url, filename):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(path):
        sys.stderr.write("downloading %s ...\n" % url)
        with open(path, "wb") as f:
            f.write(http_get(url))
    return path


def clean_text(s):
    """基础清洗：去 HTML 标签、法语字母、多余空白与不可见字符。"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in FR_EN:
        s = s.replace(a, b).replace(a.upper(), b.upper())
    s = unicodedata.normalize("NFKC", s)
    # 移除不可见字符、控制字符与私用区字符（原始 PDF 转换时残留）
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C"
                and not (0xE000 <= ord(ch) <= 0xF8FF))
    s = s.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def normalize_headword(word):
    """规范化词头：统一撇号、去除 systematic(al) / toward(s) 之类的可选后缀标注。"""
    word = word.strip().replace("’", "'").replace("ˈ", "'")
    word = re.sub(r"\([a-z]{1,3}\)$", "", word)
    return word.strip()


# --------------------------------------------------------------------------
# 1. 解析大纲词表（词头 + 音标 + 中文释义）
# --------------------------------------------------------------------------
LINE_WITH_PHONETIC = re.compile(r"^([A-Za-z][A-Za-z\-'\u2019\u02c8() ]*?)\s*\[([^\]]+)\]\s*,?\s*(.+)$")
LINE_PLAIN_MULTI = re.compile(r"^([A-Za-z][A-Za-z\-']+)\s+([a-z]{1,4}\..+)$")
LINE_PLAIN_ONE = re.compile(r"^([A-Za-z])\s+([a-z]{1,4}\..+)$")
# 大纲词表内的缩写、编号等非单词条目，直接排除
ABBREV = re.compile(r"^(?:[a-z]{1,2}\.)+$|^(?:am|bc|pm|ie|etc|vs|sb|sth)$", re.I)


def parse_outline(path):
    words, skipped = [], []
    with io.open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = clean_text(raw)
            if not line or line.startswith("(") or len(line) <= 2:
                continue
            m = LINE_WITH_PHONETIC.match(line)
            if m:
                word, phonetic, meaning = m.group(1), m.group(2).strip(), m.group(3).strip()
            else:
                m = LINE_PLAIN_MULTI.match(line) or LINE_PLAIN_ONE.match(line)
                if not m:
                    skipped.append(line)
                    continue
                word, phonetic, meaning = m.group(1), "", m.group(2).strip()
            word = normalize_headword(word)
            # 剔除缩写与释义为空/过短的记录
            if ABBREV.match(word) or not meaning or len(meaning) < 2:
                skipped.append(line)
                continue
            words.append({
                "word": word.lower(),
                "display": word,
                "phonetic": phonetic,
                "meaning": meaning,
            })
    return words, skipped


# --------------------------------------------------------------------------
# 2. 解析有道考神四级词库（读音 + 释义 + 真题例句 + 词典例句）
# --------------------------------------------------------------------------
def parse_youdao(paths, with_exam=True):
    book = {}
    for p in paths:
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                for line in z.read(name).decode("utf-8", "ignore").split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    head = clean_text(obj.get("headWord", "")).lower()
                    content = (obj.get("content", {}) or {}).get("word", {}).get("content", {}) or {}
                    if not head or not content:
                        continue
                    rec = book.setdefault(head, {})

                    phone = content.get("usphone") or content.get("ukphone") or content.get("phone")
                    if phone and not rec.get("phone"):
                        rec["phone"] = clean_text(phone).replace("'", "\u02c8")

                    exam = (content.get("realExamSentence", {}) or {}).get("sentences", []) or []
                    if with_exam and exam and not rec.get("exam"):
                        rec["exam"] = exam

                    sent = (content.get("sentence", {}) or {}).get("sentences", []) or []
                    if sent and not rec.get("sent"):
                        rec["sent"] = sent
    return book


def pick_sentences(raw_list, kind, word):
    """清洗例句：去掉省略号、统一句首大写、控制长度、去重。"""
    out, seen = [], set()
    for item in raw_list or []:
        en = clean_text(item.get("sContent", ""))
        en = re.sub(r"^[.\s\u2026]+", "", en)
        en = re.sub(r"[.\s\u2026]+$", lambda m: "." if "." in m.group(0) else "", en)
        if not en or len(en) < 12 or len(en) > 180:
            continue
        if not en.endswith((".", "?", "!")):
            en += "."
        en = en[0].upper() + en[1:]
        low = en.lower()
        # 真题例句要求句中确实出现该词（含词形变化的前缀匹配）
        if kind == "exam":
            stem = word[: max(4, len(word) - 2)]
            if word not in low and stem not in low:
                continue
        if low in seen:
            continue
        seen.add(low)
        zh = clean_text(item.get("sCn", "") or item.get("sContent_cn", ""))
        entry = {"en": en}
        if zh:
            entry["zh"] = zh
        if kind == "exam":
            info = item.get("sourceInfo", {}) or {}
            entry["source"] = format_source(info)
        out.append(entry)
        if len(out) >= (2 if kind == "exam" else 1):
            break
    return out


def format_source(info):
    year = clean_text(str(info.get("year", "")))
    m = re.match(r"^(\d{4})[.\-/]?(\d{1,2})?$", year)
    label = ""
    if m:
        y = int(m.group(1))
        # 源数据存在年份笔误（如 2107.12），统一归位到合理年份
        if y > 2050:
            y -= 100
        label = str(y)
        if m.group(2):
            label += "." + m.group(2)
    parts = [label] if label else []
    paper = clean_text(str(info.get("paper", "")))
    if paper:
        parts.append(paper)
    stype = clean_text(str(info.get("type", "")))
    if stype:
        parts.append(stype.replace("题", ""))
    return " · ".join(parts)


# --------------------------------------------------------------------------
# 3. 合并、校验并输出
# --------------------------------------------------------------------------
def main():
    outline_path = fetch(OUTLINE_URL, "CET4_edited.txt")
    yd_paths = [fetch(u, os.path.basename(u)) for u in YOUDao_URLS]
    yd_extra = [fetch(u, os.path.basename(u)) for u in YOUDao_EXTRA_URLS]

    outline, skipped = parse_outline(outline_path)
    book = parse_youdao(yd_paths, with_exam=True)
    # 补充词库只提供词典例句与音标，不参与真题例句
    for k, v in parse_youdao(yd_extra, with_exam=False).items():
        rec = book.setdefault(k, {})
        for key in ("phone", "sent"):
            if v.get(key) and not rec.get(key):
                rec[key] = v[key]

    result, seen, stats = [], set(), {
        "outlineTotal": len(outline),
        "skippedLines": len(skipped),
        "withPhonetic": 0,
        "withExam": 0,
        "withExample": 0,
        "dropped": 0,
    }

    for item in outline:
        word, display = item["word"], item["display"]
        if word in seen or not re.fullmatch(r"[a-z][a-z\-']*", word):
            stats["dropped"] += 1
            continue
        seen.add(word)

        y = book.get(word, {})
        phonetic = item["phonetic"] or y.get("phone", "")
        if phonetic and not phonetic.startswith("/"):
            phonetic = "/" + phonetic.strip("/") + "/"
        # 中文释义以大纲词表释义为准，保证释义准确且不超纲
        meaning = re.sub(r"\.{2,}", "\u2026", item["meaning"])

        exam = pick_sentences(y.get("exam"), "exam", word)
        examples = pick_sentences(y.get("sent"), "dict", word)

        entry = {"word": display, "phonetic": phonetic, "meaning": meaning}
        if exam:
            entry["exam"] = exam
        if examples:
            entry["examples"] = examples

        if entry["phonetic"]:
            stats["withPhonetic"] += 1
        if exam:
            stats["withExam"] += 1
        if exam or examples:
            stats["withExample"] += 1
        result.append(entry)

    result.sort(key=lambda e: e["word"].lower())

    payload = {
        "meta": {
            "name": "大学英语四级大纲词汇",
            "wordCount": len(result),
            "generatedAt": "2026-10-15",
            "sources": [
                {"title": "大学英语四级大纲词汇表（词头/音标/中文释义）",
                 "repo": "https://github.com/mahavivo/english-wordlists",
                 "file": "CET4_edited.txt",
                 "origin": "全国大学英语四、六级考试大纲（2016 年版）四级词表"},
                {"title": "有道考神·四级词库（音标/释义/四级真题例句）",
                 "repo": "https://github.com/kajweb/dict",
                 "file": "book/CET4_1.zip, book/CET4_2.zip, book/CET4_3.zip, book/Level4_2.zip",
                 "origin": "网易有道背单词 App 公开词库"},
            ],
            "coverage": stats,
        },
        "words": result,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    json_path = os.path.join(DATA_DIR, "words.json")
    with io.open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    js_path = os.path.join(DATA_DIR, "words.js")
    with io.open(js_path, "w", encoding="utf-8") as f:
        f.write("/* 自动生成，请勿手动修改；数据源见 README.md。原始数据见同目录 words.json */\n")
        f.write("window.CET4_WORDBOOK = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    sys.stderr.write("done: %d words -> %s\n" % (len(result), json_path))
    sys.stderr.write("stats: %s\n" % json.dumps(stats, ensure_ascii=False))
    sys.stderr.write("skipped lines: %s\n" % skipped[:20])


if __name__ == "__main__":
    main()
