"""Value-insensitive UI matching. These signatures never certify exact recovery."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit


@dataclass(frozen=True)
class NormTree:
    canonical: str
    signature: str
    tokens: tuple[str, ...]
    app: str
    url_pattern: str
    title: str


def _url(url: str) -> tuple[str, str, list[str]]:
    parsed = urlsplit(url)
    segments = parsed.path.split("/")
    ids = []
    for i, segment in enumerate(segments):
        # Numeric workflow steps identify different screens, unlike entity IDs.
        step = i > 0 and segments[i - 1] in {"form", "step", "steps"}
        tenant = i > 0 and segments[i - 1] == "t"
        opaque = re.fullmatch(
            r"\d+|[0-9a-f]{8,}|[0-9a-f-]{36}|[A-Za-z0-9_-]{16,}", segment
        )
        if segment and (tenant or (opaque and not step)):
            ids.append(unquote(segment))
            segments[i] = "{id}"
    query = "&".join(
        key + "={v}"
        for key in sorted(
            {k for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        )
    )
    pattern = parsed.netloc.lower() + "/".join(segments)
    return parsed.netloc.lower(), pattern + ("?" + query if query else ""), ids


def _text(text: str, ids: list[str]) -> str:
    for value in sorted(ids, key=len, reverse=True):
        text = re.sub(
            r"(?<![\w-])" + re.escape(value) + r"(?![\w-])",
            "{id}",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", "{date}", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "{time}", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "{email}", text)
    text = re.sub(
        r"\b[A-Z]{1,4}-\d{3,}\b|(?<!\w)#?\d{4,}\b", "{id}", text, flags=re.IGNORECASE
    )
    text = re.sub(r"\$?\d[\d,]*(?:\.\d+)?%?", "{num}", text)
    return " ".join(text.lower().split())[:40]


def normalise(tree: str) -> NormTree:
    url = title = ""
    raw = []
    for line in tree.splitlines():
        if line.startswith("url: "):
            url = line[5:]
        elif line.startswith("title: "):
            title = line[7:]
        elif line.strip():
            depth = len(line) - len(line.lstrip())
            node = line.strip().removeprefix("- ").strip()
            if node.startswith("'") and node.endswith("'"):
                node = node[1:-1].replace("''", "'")
            match = re.match(r'([a-zA-Z][a-zA-Z -]*?)(?=\s*[":\[]|$)(.*)', node)
            if not match:
                continue
            role, rest = match[1].strip().lower(), match[2].strip()
            quoted = re.match(r'"((?:[^"\\]|\\.)*)"', rest)
            name = quoted[1] if quoted else rest.lstrip(":").strip()
            level = re.search(r"\[level=(\d+)\]", rest)
            if not quoted:
                name = re.sub(r"\[[^\]]*\]", "", name).strip().rstrip(":")
            if role in {"textbox", "spinbutton", "combobox", "slider"} and not quoted:
                name = ""
            raw.append((depth, role, name, level[1] if level else None))
    app, pattern, ids = _url(url)
    lines, tokens = [], []
    skip_depth = None
    option_counts: dict[int, int] = {}
    table_depth = None
    header_seen = False
    for depth, role, name, level in raw:
        if skip_depth is not None:
            if depth > skip_depth:
                continue
            skip_depth = None
        if table_depth is not None and depth <= table_depth:
            table_depth = None
        if role == "table":
            table_depth, header_seen = depth, False
        if table_depth is not None and role == "rowgroup":
            continue  # Empty and populated tbody carry the same structural information.
        if table_depth is not None and role == "row":
            if header_seen:
                skip_depth = depth
                continue
            header_seen = True
            # A repeat marker exists even when only a header row is present.
            lines.append(f"{table_depth + 2}|row|{{repeat}}")
            tokens.append("row|{repeat}")
            depth = table_depth + 2
        if role == "combobox":
            option_counts = {depth: 0}
        if role == "option" and option_counts:
            parent = max(option_counts)
            option_counts[parent] += 1
            if option_counts[parent] > 10:
                continue
        name = _text(name, ids)
        if role in {"generic", "text"} and not name:
            continue
        if level:
            name += " [level=" + level + "]"
        lines.append(f"{depth}|{role}|{name}")
        tokens.append(f"{role}|{name}")
    title = _text(title, ids)
    canonical = pattern + "\n" + title + "\n" + "\n".join(lines)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return NormTree(canonical, digest, tuple(sorted(tokens)), app, pattern, title)


def signature(tree: str) -> str:
    return normalise(tree).signature


def fuzzy_match(a, b) -> float:
    left, right = Counter(a), Counter(b)
    total = sum((left | right).values())
    return sum((left & right).values()) / total if total else 0.0
