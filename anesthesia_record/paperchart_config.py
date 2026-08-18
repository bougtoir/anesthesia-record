"""paperChart 設定ファイル (s5.txt / b650.txt 形式) のローダと検証.

paperChart（斎藤智彦先生のフリー自動麻酔記録ソフト）の Datex-Ohmeda S/5
ドライバは、Shift-JIS の設定ファイルで DRI(Datex Record Interface) 物理データ
レコードのバイトオフセット・スケール・ラベルを定義する。GE CARESCAPE B650
(Bx50) も同一の DRI プロトコルを出力するため、同形式の ``b650.txt`` を用いる。

本モジュールは設定ファイルを構造化して読み込み、内部整合性を検証する。
paperChart 本体（C言語）の完全なパーサではなく、設定ファイルが構文的・
参照的に破綻していないことを確認するための補助ツールである。

ファイル書式（概略）:
- ``//`` から行末まではコメント。
- ``rs232c_port = com1: ;`` のようなキー=値行。
- ``numerics { ... }`` / ``labels { ... }`` / ``waves { ... }`` のブロック。
- ``initial_waves = 1/4/9 ;`` の初期表示波形（waves の subrecord type を参照）。
- ``err_xxx = <秒> "文字列" "文字列" ... ;`` のエラーメッセージ定義。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENCODING = "cp932"


@dataclass(frozen=True)
class NumericEntry:
    """numerics ブロック 1 行分（DRI physdata 上の 1 パラメータ）."""

    name: str
    subrecord_class: int
    group_offset: int
    labelset: str
    scale: float
    unit: str
    raw_tokens: tuple[str, ...]


@dataclass(frozen=True)
class WaveEntry:
    """waves ブロック 1 行分."""

    name: str
    subrecord_type: int
    samples_per_sec: int
    scale: float
    unit: str
    display_low: float
    display_high: float
    calib_low: float
    calib_high: float


@dataclass
class PaperChartConfig:
    """paperChart 設定ファイル全体."""

    rs232c_port: str
    numerics: list[NumericEntry] = field(default_factory=list)
    labels: dict[str, list[str]] = field(default_factory=dict)
    waves: list[WaveEntry] = field(default_factory=list)
    initial_waves: list[int] = field(default_factory=list)
    messages: dict[str, list[str]] = field(default_factory=dict)

    def numeric(self, name: str) -> NumericEntry | None:
        """名前で numerics を検索（同名複数ある場合は最初の 1 件）."""
        for entry in self.numerics:
            if entry.name == name:
                return entry
        return None

    def wave(self, name: str) -> WaveEntry | None:
        for entry in self.waves:
            if entry.name == name:
                return entry
        return None

    def validate(self) -> list[str]:
        """内部整合性を検証し、問題点の一覧を返す（空なら健全）."""
        problems: list[str] = []

        if not self.rs232c_port:
            problems.append("rs232c_port が空です")

        if not self.numerics:
            problems.append("numerics に有効な行がありません")

        # numerics が参照する labelset が labels に定義されているか
        known_labels = set(self.labels)
        for entry in self.numerics:
            if entry.labelset and entry.labelset not in known_labels:
                problems.append(
                    f"numerics '{entry.name}' が未定義の labelset "
                    f"'{entry.labelset}' を参照しています"
                )

        # initial_waves が waves の subrecord type に解決できるか
        known_wave_types = {w.subrecord_type for w in self.waves}
        for wtype in self.initial_waves:
            if wtype not in known_wave_types:
                problems.append(
                    f"initial_waves の subrecord type {wtype} が "
                    f"waves に存在しません"
                )

        return problems


def _strip_comments(text: str) -> str:
    """``//`` 以降を除去する（文字列リテラル内は保護）."""
    out_lines: list[str] = []
    for line in text.splitlines():
        result: list[str] = []
        in_str = False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                in_str = not in_str
                result.append(ch)
                i += 1
                continue
            if (
                not in_str
                and ch == "/"
                and i + 1 < len(line)
                and line[i + 1] == "/"
            ):
                break
            result.append(ch)
            i += 1
        out_lines.append("".join(result))
    return "\n".join(out_lines)


def _parse_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _parse_numeric_entry(name: str, rhs: str) -> NumericEntry:
    """numerics の右辺トークン列を構造化する.

    トークン数は行により 7〜8 個と揺れるため、確実に決まる先頭 2 個
    (class, group_offset) と、末尾から解釈できる scale/unit を取り出す。
    """
    tokens = rhs.split()
    if len(tokens) < 3:
        raise ValueError(f"numerics '{name}' のトークンが不足: {rhs!r}")

    subrecord_class = int(tokens[0])
    group_offset = int(tokens[1])

    labelset = ""
    if len(tokens) >= 3:
        third = tokens[2]
        if third.startswith('"') and third.endswith('"'):
            labelset = third.strip('"')
        elif not third.lstrip("-").isdigit() and _parse_float(third) is None:
            labelset = third

    # 末尾トークンが数値なら unit 省略・そのトークンが scale。
    # そうでなければ末尾が unit、その手前が scale。
    scale = 1.0
    unit = ""
    last = tokens[-1]
    last_val = _parse_float(last)
    if last_val is not None:
        scale = last_val
    else:
        unit = last
        prev_val = _parse_float(tokens[-2]) if len(tokens) >= 2 else None
        if prev_val is not None:
            scale = prev_val

    return NumericEntry(
        name=name,
        subrecord_class=subrecord_class,
        group_offset=group_offset,
        labelset=labelset,
        scale=scale,
        unit=unit,
        raw_tokens=tuple(tokens),
    )


def _split_entries(block_body: str) -> list[tuple[str, str]]:
    """``name = ... ;`` を (name, rhs) の並びに分解する."""
    entries: list[tuple[str, str]] = []
    for stmt in block_body.split(";"):
        stmt = stmt.strip()
        if not stmt or "=" not in stmt:
            continue
        name, rhs = stmt.split("=", 1)
        entries.append((name.strip(), rhs.strip()))
    return entries


def _extract_block(text: str, name: str) -> str | None:
    """``name { ... }`` の中身を返す."""
    match = re.search(rf"{name}\s*\{{(.*?)\}}", text, re.DOTALL)
    return match.group(1) if match else None


def _parse_labels(block_body: str) -> dict[str, list[str]]:
    labels: dict[str, list[str]] = {}
    for name, rhs in _split_entries(block_body):
        tokens = re.findall(r'"[^"]*"|\S+', rhs)
        labels[name] = [t.strip('"') for t in tokens]
    return labels


def _parse_waves(block_body: str) -> list[WaveEntry]:
    waves: list[WaveEntry] = []
    for name, rhs in _split_entries(block_body):
        tokens = rhs.split()
        if len(tokens) < 8:
            raise ValueError(f"waves '{name}' のトークンが不足: {rhs!r}")
        waves.append(
            WaveEntry(
                name=name,
                subrecord_type=int(tokens[0]),
                samples_per_sec=int(tokens[1]),
                scale=float(tokens[2]),
                unit=tokens[3],
                display_low=float(tokens[4]),
                display_high=float(tokens[5]),
                calib_low=float(tokens[6]),
                calib_high=float(tokens[7]),
            )
        )
    return waves


def _parse_messages(text: str) -> dict[str, list[str]]:
    messages: dict[str, list[str]] = {}
    for match in re.finditer(r"(err_\w+)\s*=\s*(.*?);", text, re.DOTALL):
        name = match.group(1)
        body = match.group(2)
        strings = re.findall(r'"([^"]*)"', body)
        messages[name] = strings
    return messages


def parse_paperchart_config(text: str) -> PaperChartConfig:
    """設定ファイル本文（デコード済み文字列）を構造化する."""
    clean = _strip_comments(text)

    port_match = re.search(r"rs232c_port\s*=\s*([^;]+);", clean)
    rs232c_port = port_match.group(1).strip() if port_match else ""

    numerics: list[NumericEntry] = []
    num_block = _extract_block(clean, "numerics")
    if num_block is not None:
        for name, rhs in _split_entries(num_block):
            numerics.append(_parse_numeric_entry(name, rhs))

    labels: dict[str, list[str]] = {}
    lbl_block = _extract_block(clean, "labels")
    if lbl_block is not None:
        labels = _parse_labels(lbl_block)

    waves: list[WaveEntry] = []
    wave_block = _extract_block(clean, "waves")
    if wave_block is not None:
        waves = _parse_waves(wave_block)

    initial_waves: list[int] = []
    iw_match = re.search(r"initial_waves\s*=\s*([^;]+);", clean)
    if iw_match:
        initial_waves = [
            int(tok) for tok in iw_match.group(1).split("/") if tok.strip().isdigit()
        ]

    messages = _parse_messages(clean)

    return PaperChartConfig(
        rs232c_port=rs232c_port,
        numerics=numerics,
        labels=labels,
        waves=waves,
        initial_waves=initial_waves,
        messages=messages,
    )


def load_paperchart_config(
    path: str | Path, encoding: str = DEFAULT_ENCODING
) -> PaperChartConfig:
    """設定ファイル（既定 Shift-JIS/cp932）を読み込み構造化する."""
    raw = Path(path).read_bytes()
    return parse_paperchart_config(raw.decode(encoding))
