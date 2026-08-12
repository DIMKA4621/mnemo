"""Retrieval quality evaluation on a REAL bank — a measuring tool, not a test.

`test_search.py` is a regression floor and does that job. It cannot compare:
its recall@3/@5 sit at 1.00 for three different model x runtime pairs, missing
the same two cases each time (see topics/embedding-throughput.md). This file
exists to tell configurations apart.

Three things make it able to:

1. **It runs on a real bank** — this repository's own `.claude/memory` by
   default — not on a synthetic fixture written to be findable.
2. **Ground truth is a phrase, not a path.** A chunk is relevant when its text
   contains the case's `anchor`. That survives re-chunking (so a chunking
   change can be measured with the same ruler), and it handles the same fact
   being recorded in two places, which a path-based label gets wrong.
   It also cannot rot silently: an anchor that matches nothing is a loud
   error, not a quietly lower score.
3. **It scores each retrieval leg separately.** Vector alone, lexical alone,
   and both fused — so a change to one leg is visible instead of averaged
   away.

Not a pytest test and deliberately not named `test_*`: it needs the model and
this repo's memory, so it can neither run in CI nor gate a commit.

The bank it measures is a living one — writing memory changes the corpus and
moves every number by a case or so. Compare arms WITHIN a run, not a number
against one written down last week.

    .venv/Scripts/python tests/eval_search.py [--bank DIR] [--verbose]
"""
from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import RRF_VECTOR_WEIGHT, resolve  # noqa: E402
from src.providers import get_provider  # noqa: E402
from src.search import _fts_ranked, _rrf, _vector_ranked  # noqa: E402
from src.store import connect  # noqa: E402

DEFAULT_BANK = Path(__file__).resolve().parent.parent / ".claude" / "memory"

# Retrieval depth. `search()` widens the candidate pool to `top_k * 4` when no
# path_prefix is given; the same formula is used here so the numbers transfer.
TOP_K = 10
POOL = max(TOP_K * 4, 20)


@dataclass
class Case:
    """One labeled query.

    `query` is a PARAPHRASE — how somebody who does not remember the wording
    would ask. It must not reuse the anchor's content words, or the lexical
    leg is being handed the answer. `check_leak()` enforces that.

    `anchor` is copied verbatim out of the bank and defines relevance.
    `note` says which file it was taken from — documentation only, never used
    in scoring, because the same fact may legitimately live in two files.
    """

    query: str
    anchor: str
    note: str = ""


CASES: list[Case] = [
    # ---------------------------------------------------- embedding throughput
    Case("наскільки відеокарта виграє на масовому ембедингу",
         "GPU швидша у 8.8 раза", "throughput"),
    Case("чому дві різні мережі взагалі можна ставити поруч у вимірі",
         "дотреновані XLM-RoBERTa-large", "throughput"),
    Case("чи обрізався якийсь фрагмент при нинішніх налаштуваннях",
         "Жоден чанк не перевищив 512", "throughput"),
    Case("чи стане пошук помітно спритнішим на відеокарті",
         "875 мс проти 497 мс, тобто лише 1.8×", "throughput"),
    Case("чи можна задіяти прискорення на карті AMD під віндою",
         "ні CUDA, ні ROCm під Windows", "throughput"),
    Case("у якому порядку подавати тексти, щоб не палити час даремно",
         "Сортування дало +36%", "throughput"),
    Case("від чого насправді залежить тривалість перебудови",
         "константа з розкидом 1.4%", "throughput"),
    Case("чи треба підбирати число замірами, чи його можна порахувати",
         "оптимальне розбиття рахується аналітично", "throughput"),
    Case("яка частка роботи йде намарно при поточній поведінці",
         "2.05× від дна", "throughput"),
    Case("чому дві попередні спроби виміру провалились",
         "перший же файл на 73 чанки вичерпував бюджет", "throughput"),
    Case("що станеться з довгими фрагментами, якщо підняти стелю",
         "мовчки обрізаються", "throughput"),
    Case("перший запит після простою відповідає довго",
         "штраф 5.26 с", "throughput"),
    Case("чи стане гірше, якщо взяти іншу мережу",
         "гіпотеза «перейдемо й просяде якість» не", "throughput"),
    Case("чи могла нагріта машина зіпсувати заміри затримки",
         "тепловий тротлінг", "throughput"),
    # --------------------------------------------------------------- deferred
    Case("що заважає пообіцяти «склонував і поставив»",
         "клонує й поставить v2", "deferred"),
    Case("один із тестів червоний, хоча сам код справний",
         "продукт бере токен зі змінної й файла не пише", "deferred"),
    Case("служба одного разу не зайняла порт після оновлення",
         "Схоже на гонку за порт зі старим serving-процесом", "deferred"),
    Case("яких проєктів діагностика не побачить у принципі",
         "проєкт, який ніколи не відкривали в Claude Code, невидимий",
         "deferred"),
    Case("чужий репозиторій зі старою розкладкою тек памʼяті",
         "Банк `maria` вказує на", "deferred"),
    # ------------------------------------------------------------- CI failures
    Case("у мене все зелене, а на віддаленому ранері червоне",
         "Локально на цій же Windows-машині — 200/200", "ci"),
    Case("звідки у згенерованому файлі взялись зайві символи",
         "409 проти 395 байтів = 7 підстановок × 2 лапки", "ci"),
    Case("як обійшли розбіжність старої й нової оболонки при заміні",
         "переписано на функцію `substitute()`", "ci"),
    Case("командлет є в одній консолі й відсутній в іншій",
         "задає той, хто тебе запустив", "ci"),
    Case("як дістати повний текст кроку зі збірки без прав адміна",
         "редирект на підписаний blob", "ci"),
    Case("чому вивід сторінки збирається неповним",
         "лог віртуалізований, скрол губить", "ci"),
    Case("оболонка на ранері виявилась не тією, що очікували",
         "без дистрибутива він виходить з 1", "ci"),
    # ------------------------------------------------------------- uninstaller
    Case("скільки місця звільнилось при повному знесенні",
         "Знесено 2.4 ГіБ", "uninstall"),
    Case("чому оновлення поверх наявного нічого не доводить",
         "воно працює на тому, що вже зарезолвлено", "uninstall"),
    Case("вбив породжений процес, а робота триває далі",
         "редиректор-заглушка", "uninstall"),
    Case("як переконатись, що бекенд справді приймає запити",
         "чекає на `/health`", "uninstall"),
    Case("яким має бути крок знесення, коли все вже поламане",
         "Жоден крок не залежить від попереднього", "uninstall"),
    Case("чи збігся вміст після повторної побудови з нуля",
         "Індекс відтворився байт-у-байт за змістом", "uninstall"),
    # --------------------------------------------------------------- installer
    Case("як лишити згоду на важке завантаження явною, але без окремого кроку",
         "Розвʼязано питанням `[Y/n]`", "install"),
    Case("резидент лежить одразу після встановлення — це поломка",
         "down (starts on first search)", "install"),
    Case("чим має завершуватись встановлення",
         "Останнє на екрані — вивід `doctor`", "install"),
    Case("чому не можна ставити питання в неінтерактивному запуску",
         "Запит, якого ніхто не бачить", "install"),
    # --------------------------------------------------------------- migration
    Case("чи можна відновити, якому проєкту належав старий файл індексу",
         "sha1 не обертається", "migration"),
    Case("чому імена тек у сховищі не годяться як джерело шляхів",
         "не декодується назад — роздільники втрачені", "migration"),
    Case("що саме не давало колонці звузитись",
         "має `white-space: nowrap`", "migration"),
    Case("два банки в одному проєкті — що мусить різнитись, а що ні",
         "хост і порт лишити спільними", "migration"),
    Case("прибрав теку й додав знову, а індекс не будується",
         "лишався скасованим до кінця життя процесу", "migration"),
    Case("перевірка на термінал обманює під однією оболонкою",
         "бреше під Git Bash", "migration"),
    Case("як відрізнити, що файл правили руками",
         "sha256 байтів файлу", "migration"),
    Case("звідки взялось по дві контрольні суми на редакцію",
         "має CRLF там, де Linux-проєкт має LF", "migration"),
    Case("як не втратити чужі записи при переході на шар шаблонів",
         "починається з наявного", "migration"),
    # --------------------------------------------------------------------- cli
    Case("команда з кореня проєкту не знаходила куди дивитись",
         "дивиться в обидва боки", "cli"),
    Case("відносний шлях розгортався не туди, бо його розгортав не той",
         "а в сервісу свій `cwd`", "cli"),
    Case("як заховати службову команду з довідки",
         "не ховає його, а друкує `==SUPPRESS==`", "cli"),
    # ------------------------------------------------------------------- hooks
    Case("скільки старих вписувань доводиться знімати",
         "`_RETIRED_HOOKS` тепер чотири пари", "hooks"),
    Case("навіщо прибрали автоматичне підкидання контексту",
         "віддає тул `tree`", "hooks"),
    # ----------------------------------------------------------------- orphans
    Case("чим шкідливий покинутий файл індексу",
         "Орфан інертний: не в пошуку", "orphans"),
    Case("чому не видаляти автоматично, коли теки більше немає",
         "40 хв перебудови замість `git checkout`", "orphans"),
    # ------------------------------------------------------------------- misc
    Case("чому команда більше не відкриває вікно браузера",
         "Прибрано `webbrowser.open`", "docs"),
    Case("чому POSIX-скрипт неможливо було прогнати тут",
         "кладе venv у `.venv/bin`", "installers"),
    Case("що саме звіряє наскрізна перевірка конвеєра",
         "дослівно дорівнює збереженому вмісту", "pipeline"),
    Case("старий скрипт у вже адоптованому проєкті лишиться зі старою вадою",
         "виправлення підстановки під bash 3.2 не доїхало", "refresh"),
    Case("виправлення не доїхало, бо виклик стоїть не в тій гілці",
         "код оновлення я поклав у `_bootstrap_layer`", "refresh"),
    Case("де запамʼятовується вибір оформлення",
         "`localStorage['mnemo_theme']`", "theme"),
    Case("правило кольору перебивалось загальним, і винен порядок",
         "`.btn` завжди перебивав", "theme"),
    Case("секрет одного разу поїхав у репозиторій",
         "поїхало в git-трекований `.mcp.json`", "2026-07-30"),
    Case("який ключ які двері відчиняє",
         "Матриця: `/mcp` — тільки токен банку", "2026-07-31"),
    Case("чому команду перебудови винесли з проєктного обличчя",
         "`reindex` переїхав на адмінку", "2026-07-31"),
    Case("небезпечний випадок — коли розмірність збігається",
         "e5-large і bge-m3 обидві 1024", "provider-settings"),
    Case("як показати, що налаштування зараз не діє",
         "значення перекрите змінною `MNEMO_PROVIDER`", "provider-settings"),
]


# ------------------------------------------------------------------- matching

_WS = re.compile(r"\s+")
_WORD = re.compile(r"\w+", re.UNICODE)


def norm(text: str) -> str:
    """Collapse whitespace and case so an anchor may span a wrapped line."""
    return _WS.sub(" ", text).strip().lower()


def content_words(text: str) -> set[str]:
    """Words long enough to carry meaning — used only for the leak check."""
    return {w for w in _WORD.findall(text.lower()) if len(w) > 4}


# ---------------------------------------------------------------------- arms


def _fts_phrase_ranked(
    conn: sqlite3.Connection, query: str, limit: int
) -> list[int]:
    """The lexical leg AS IT WAS before A1 — the whole query as one phrase.

    Kept so the fix can be measured against its predecessor in the same run,
    instead of by stashing the change and re-running from memory.
    """
    match = '"' + query.replace('"', '""') + '"'
    try:
        rows = conn.execute(
            "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ? "
            "ORDER BY bm25(fts_chunks) LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["rowid"] for r in rows]


def _fused(*rankings: list[int]) -> list[int]:
    scores = _rrf(*rankings)
    return sorted(scores, key=lambda c: scores[c], reverse=True)


def _fused_weighted(pairs: list[tuple[float, list[int]]]) -> list[int]:
    """Weighted fusion, through the product's own `_rrf` so the two cannot
    drift — an evaluation that reimplements what it measures is measuring
    itself."""
    weights = tuple(w for w, _ in pairs)
    scores = _rrf(*(r for _, r in pairs), weights=weights)
    return sorted(scores, key=lambda c: scores[c], reverse=True)


# -------------------------------------------------------------------- metrics


def measure(ranking: list[int], relevant: set[int]) -> dict[str, float]:
    ranks = [i for i, cid in enumerate(ranking[:TOP_K]) if cid in relevant]
    first = ranks[0] if ranks else None
    dcg = sum(1.0 / math.log2(i + 2) for i in ranks)
    ideal = sum(
        1.0 / math.log2(i + 2) for i in range(min(len(relevant), TOP_K))
    )
    return {
        "r@1": float(first == 0),
        "r@3": float(first is not None and first < 3),
        "r@5": float(first is not None and first < 5),
        "r@10": float(first is not None),
        "mrr": 1.0 / (first + 1) if first is not None else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
    }


METRICS = ("r@1", "r@3", "r@5", "r@10", "mrr", "ndcg")


def _arm(weight: float) -> str:
    """Arm label for a vector:lexical weight; the shipped one is starred."""
    return f"hybrid-{weight:g}:1" + ("*" if weight == RRF_VECTOR_WEIGHT else "")


# ----------------------------------------------------------------- validation


def check_leak() -> list[str]:
    """Report cases whose question reuses the answer's own vocabulary.

    A leak does not invalidate the case, but it flatters the lexical leg, so
    it has to be visible rather than silently baked into the score.
    """
    return [
        f"    {c.query[:56]:<56} shares {sorted(shared)}"
        for c in CASES
        if (shared := content_words(c.query) & content_words(c.anchor))
    ]


def build_relevance(conn: sqlite3.Connection) -> tuple[dict[int, set[int]], list[str]]:
    """Map each case to the chunks that literally contain its anchor."""
    rows = conn.execute("SELECT id, path, content FROM chunks").fetchall()
    haystack = [(r["id"], r["path"], norm(r["content"])) for r in rows]
    relevance: dict[int, set[int]] = {}
    problems: list[str] = []
    for i, case in enumerate(CASES):
        needle = norm(case.anchor)
        found = {cid for cid, _, text in haystack if needle in text}
        relevance[i] = found
        if not found:
            problems.append(
                f"    MISSING anchor ({case.note}): {case.anchor[:60]!r}"
            )
        elif len(found) > 3:
            paths = sorted({p for cid, p, _ in haystack if cid in found})
            problems.append(
                f"    BROAD anchor ({case.note}): {len(found)} chunks in "
                f"{len(paths)} files — {case.anchor[:40]!r}"
            )
    return relevance, problems


# --------------------------------------------------------------------- runner


def run(bank: Path, verbose: bool) -> int:
    paths = resolve(str(bank))
    if not Path(paths.db).exists():
        print(f"no index for {bank} — register the bank and let it build")
        return 2
    conn = connect(paths.db, ensure=False)
    try:
        total = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        print(f"bank {bank}  chunks={total}  cases={len(CASES)}  top_k={TOP_K}")

        leaks = check_leak()
        if leaks:
            print(f"\nvocabulary shared between question and anchor "
                  f"({len(leaks)} case(s) — favours the lexical leg):")
            print("\n".join(leaks))

        relevance, problems = build_relevance(conn)
        if problems:
            print("\nground truth problems:")
            print("\n".join(problems))
        missing = sum(1 for i in relevance if not relevance[i])
        if missing:
            print(f"\n{missing} anchor(s) match nothing — the memory changed "
                  f"under the cases. Fix them before trusting the numbers.")

        provider = get_provider()
        # 1:1 is the textbook equal vote — worth keeping visible, because it
        # is the form that loses to the vector leg on its own.
        weights = (1.0, 2.0, 4.0, RRF_VECTOR_WEIGHT, 8.0, 12.0)
        arms = (("vector", "lexical", "phrase-fts")
                + tuple(_arm(w) for w in weights))
        totals = {a: {m: 0.0 for m in METRICS} for a in arms}
        rows: list[str] = []

        for i, case in enumerate(CASES):
            rel = relevance[i]
            qvec = provider.embed_query(case.query)
            vec = _vector_ranked(conn, qvec, POOL)
            lex = _fts_ranked(conn, case.query, POOL, None)
            phrase = _fts_phrase_ranked(conn, case.query, POOL)
            rankings = {
                "vector": vec,
                "lexical": lex,
                "phrase-fts": _fused(vec, phrase),
            }
            for w in weights:
                rankings[_arm(w)] = _fused_weighted(
                    [(w, vec), (1.0, lex)])
            per_arm = {a: measure(rankings[a], rel) for a in arms}
            for a in arms:
                for m in METRICS:
                    totals[a][m] += per_arm[a][m]

            if verbose or not rel:
                flag = "  NO-GT" if not rel else ""
                rows.append(
                    f"  {case.query[:48]:<48} "
                    f"v={per_arm['vector']['mrr']:.2f} "
                    f"l={per_arm['lexical']['mrr']:.2f} "
                    f"h={per_arm[_arm(RRF_VECTOR_WEIGHT)]['mrr']:.2f} "
                    f"p={per_arm['phrase-fts']['mrr']:.2f}"
                    f"  |R|={len(rel)}{flag}"
                )
        if rows:
            print("\nper case (MRR@10 by arm):")
            print("\n".join(rows))

        n = len(CASES)
        print(f"\n{'arm':<15} " + "  ".join(f"{m:>5}" for m in METRICS))
        print("-" * (15 + 8 * len(METRICS)))
        for a in arms:
            print(f"{a:<15} " + "  ".join(
                f"{totals[a][m] / n:5.2f}" for m in METRICS))

        print("\n'phrase-fts' is the lexical leg as it was before A1 — it "
              "scores identically\nto 'vector' because it contributed "
              "nothing. '*' is the shipped weight "
              f"({RRF_VECTOR_WEIGHT:g}:1).")
        return 0 if not missing else 1
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", default=str(DEFAULT_BANK))
    ap.add_argument("--verbose", action="store_true",
                    help="print every case, not just the broken ones")
    args = ap.parse_args()
    return run(Path(args.bank), args.verbose)


if __name__ == "__main__":
    sys.exit(main())
