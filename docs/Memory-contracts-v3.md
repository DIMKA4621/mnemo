# mnemo v3 — контракти (module map + API)

> **Четвертий документ трійки.** Три попередні відповідають на «що і чому»
> (`Memory-design-v3.md`), «що мусить триматись» (`Memory-requirements-v3.md`) і
> «як та в якому порядку» (`Memory-implementation-v3.md`). Останній свідомо
> лишив на вхід у фазу «точні сигнатури ендпоінтів, схему реєстру, формат
> WS-повідомлень». **Цей документ їх фіксує** — щоб чотири розробники могли
> писати паралельно, не стикаючись файлами й не вгадуючи форму даних сусіда.
>
> **Статус:** контракти, не реалізація. Тут немає жодного рядка робочого коду —
> лише сигнатури, схеми, форми запитів/відповідей і межі власності.
>
> **Позначка `[NEW]`** — рішення, якого **немає** в трьох джерельних документах;
> його вигадано тут як найпростіше, що задовольняє FR/NFR. Усе з `[NEW]` тимлід
> має проревʼювати. Усе без позначки — цитата або прямий наслідок уже
> зафіксованого рішення.
>
> Мова: проза українська, ідентифікатори / JSON / сигнатури — англійською
> (правило `.claude/rules/v3-build.md`).

---

## 0. Як користуватись цим документом

* **Перед тим як створити або змінити файл** — знайди його в розділі 1. Якщо
  він не твій, не чіпай: напиши тимліду.
* **Перед тим як викликати чужий модуль** — читай його розділ. Форма даних тут
  нормативна; якщо реалізація змушує від неї відступити — **зупинись і скажи
  тимліду**, не міняй контракт мовчки.
* Розділи 2–12 пронумеровані за блоками A–L з `Memory-implementation-v3.md`,
  щоб їх було видно один в один.

---

## 1. Карта модулів і власність файлів

Один файл — **один власник**. Це головний механізм проти колізій.

| Файл / тека | Блок | Фаза | Власник |
|---|---|---|---|
| `src/config.py` | — | 0 | **engine-dev** (секційна власність, див. нижче) |
| `src/chunker.py` | — | 0 | engine-dev |
| `src/embedder.py` | — | 0 | engine-dev |
| `src/embed_server.py` | A | 0, 7 | engine-dev |
| `src/providers/__init__.py` | B | 0 | engine-dev |
| `src/providers/base.py` | B | 0 | engine-dev |
| `src/providers/local.py` | B | 0 | engine-dev |
| `src/providers/api.py` | B | 7 | engine-dev |
| `src/store.py` | C | 0 | engine-dev |
| `src/index.py` | D | 1 | engine-dev |
| `src/search.py` | H | 0, 2 | engine-dev |
| `src/registry.py` | G | 2 | **service-dev** |
| `src/servicelog.py` | I | 2 | service-dev |
| `src/workqueue.py` | E | 3 | service-dev |
| `src/watcher.py` | F | 3 | service-dev |
| `src/api.py` | J | 2, 3 | service-dev |
| `src/mcp_server.py` | K | 4 | service-dev |
| `src/mcp_admin.py` | K | 4 | service-dev **[NEW]** — адмінське обличчя MCP (10.5) |
| `src/client.py` | K | 4 | service-dev |
| `src/cli.py` | K | 4 | service-dev |
| `src/scaffold.py` | K | 4 | service-dev **[NEW]** — не було в мапі тимліда |
| `src/inject_log.py` | — | 2 | service-dev — **видаляється** (див. 8.5) **[NEW]** |
| `src/service_ctl.py` | L | 5 | **platform-dev** |
| `install.sh`, `install.ps1`, `requirements.txt` | — | 5 | platform-dev **[NEW]** |
| `src/webui/**` | — | 6 | **ui-dev** |
| `tests/**` | — | усі | **tester** |
| `docs/**`, `README.md`, `CLAUDE.md`, `.claude/skills/mnemo-adopt/**` | — | усі | **docs-keeper** |

**Розбіжності з мапою тимліда — три, усі додаткові, жодної заміни:**

1. `src/scaffold.py` (`mnemo init`) у мапі не було, а його вміст у v3 міняється
   суттєво (`.mcp.json` стає URL+токен, хуки `ingest`/`hook-postedit` зникають).
   Логічно віддати service-dev разом з рештою блоку K.
2. `src/inject_log.py` у мапі не було. Блок I (`servicelog.py`) його **замінює**
   цілком — JSONL зникає (це прямо в `Memory-implementation-v3.md`: «**SQLite
   `service.db`** (заміняє JSONL)»). Тому файл видаляється у фазі 2, а не
   доживає паралельно.
3. `install.sh` / `install.ps1` / `requirements.txt` — власність platform-dev.
   **Нові залежності ніхто не додає сам:** потрібен пакет → запит до
   platform-dev.

### 1.1 `src/config.py` — секційна власність **[NEW]**

`config.py` — єдиний файл, який фізично потрібен усім. Другий конфіг-модуль
завести не можна: це паралельний шлях, заборонений правилами. Тому фіксуємо
**власність по секціях**: кожна секція починається банером-коментарем із
власником, і редагувати можна **тільки свою**.

```
# --- paths & state ------------------------------  engine-dev
# --- embedding model & daemon (A) ---------------  engine-dev
# --- chunking & search knobs --------------------  engine-dev
# --- providers (B) ------------------------------  engine-dev
# --- registry & banks (G) -----------------------  service-dev
# --- api / websocket (J) ------------------------  service-dev
# --- queue & watcher (E, F) ---------------------  service-dev
# --- service log & retention (I) ----------------  service-dev
# --- process lifecycle (L) ----------------------  platform-dev
```

Порядок секцій — фіксований (як вище), нові ключі дописуються **в кінець своєї
секції**. Це не усуває конфлікт злиття повністю, але робить його
позиційно-локальним.

---

## 2. Блок B — інтерфейс ембединг-провайдера

`src/providers/base.py`:

```python
class EmbeddingUnavailable(RuntimeError):
    """Provider cannot produce vectors right now (daemon down, API error,
    model not cached). Callers degrade; they never crash."""


class EmbeddingProvider(abc.ABC):
    name: str          # stable identity, e.g. "local" | "api"
    model: str         # e.g. "intfloat/multilingual-e5-large"
    dim: int           # vector dimensionality

    @property
    def key(self) -> str:
        """Rebuild fingerprint stored in the bank DB: f"{name}:{model}:{dim}"."""

    @abc.abstractmethod
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed documents for indexing. Raises EmbeddingUnavailable."""

    @abc.abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed one search query. Raises EmbeddingUnavailable."""

    def health(self) -> bool:
        """Cheap liveness probe; never raises."""
```

**Контракти, які мусять триматись:**

* `embed_passages` повертає рівно `len(texts)` векторів, у тому самому порядку,
  кожен довжиною `dim`. Порушення — це `EmbeddingUnavailable`, не мовчазний
  коротший список.
* Порожній `texts` → порожній список, без звернення до моделі.
* **Провайдер ніколи не качає модель.** Правило «модель тягне тільки явний
  `warmup`» лишається інваріантом (design §1, §12).
* Провайдер **не логує** й не знає ні про банки, ні про чергу.

### 2.1 `local` — як він досягає `embed_server`

`src/providers/local.py` — тонка обгортка над **уже наявним** транспортом
`embed_server.py`, без зміни протоколу:

1. `embed_passages_via_server(texts)` → якщо не `None`, повертаємо.
2. `None` (резидент недоступний) → якщо `embedder.is_model_cached()` — рахуємо
   в процесі (`embedder.embed_passages`). Це збережений з v2 запобіжник для
   тестів / офлайну.
3. Інакше — `raise EmbeddingUnavailable`. **[NEW]** У v2 цей шлях повертав
   `None` або кидав `RuntimeError` з місця виклику; у v3 єдиний тип помилки,
   бо її ловить воркер черги й API.

Автостарт резидента лишається як є (`_obtain_socket` → `_spawn_server`), з
обмеженням «тільки loopback» (`EMBED_HOST_IS_LOCAL`).

**Тихий фолбек — заборонений. Два різні випадки, два різні повідомлення.**
**[NEW]** Резидент може померти **нижче Python** (segfault в ONNX Runtime,
переповнення стеку): стандартний вивід у нього на devnull, тож раніше така
смерть зникала безслідно, а єдиним симптомом був **мовчазний** фолбек у
in-process модель — друга копія ~2.2 ГБ і ~50× повільніше. Саме цей клас
відмови коштував проєкту найбільше часу, тому:

* резидент вмикає `faulthandler` і пише нативний дамп у
  **`~/.claude/mnemo/embed-crash.log`** (дескриптор тримається відкритим на
  весь час життя процесу — із сигнального обробника переоткривати небезпечно).
  Жорсткий `taskkill /F` він перехопити не може, і це теж інформація: **порожній
  лог після зникнення = мене хтось убив**, дамп = **я впав сам**;
* клієнт **розрізняє** «помер посеред запиту» і «недосяжний з самого початку».
  Перше — це поломка, і про неї пишеться в stderr явно, з посиланням на лог.
  Друге — звичайна відсутність резидента. Для викликача обидва шляхи
  закінчуються фолбеком, але лише один із них щось означає;
* діагностика **ніколи** не є причиною, чому резидент не піднявся: помилка
  відкриття лог-файлу ковтається.

### 2.2 `api` — зовнішній провайдер (фаза 7)

`src/providers/api.py`, `httpx`, OpenAI-сумісна форма
`POST {MNEMO_API_EMBED_URL} {"model": ..., "input": [...]}` →
`{"data": [{"embedding": [...]}, ...]}`. `dim` береться з
`MNEMO_API_EMBED_DIM` (обовʼязковий, бо схема `vec0` статична). Жодного
префікса `passage:`/`query:` — це специфіка e5 і живе в `local`. **[NEW]**

### 2.3 Вибір провайдера

```python
def get_provider(spec: str | None = None) -> EmbeddingProvider
```

Пріоритет: аргумент `spec` → поле `provider` банку в реєстрі → env
`MNEMO_PROVIDER` → `"local"`. Інстанси кешуються по `spec` (один провайдер на
процес-бекенд). Значення `spec`: `"local"` або `"api"`.

**Інваріант розмірності (design §4):** `provider.key` пишеться у `meta` бази
банку. Розбіжність збереженого й активного `key` → **повна перебудова індексу
цього банку** (розділ 3.3).

---

## 3. Блок C — схема бази банку

Один файл на банк: `STATE_DIR / f"{bank_id}.db"`. `scope` / `agent_name`
зникають (банк плаский, рішення #13).

### 3.1 CREATE TABLE

```sql
-- Service/schema + bank + provider metadata. A provider or schema change is
-- detectable here, which is what triggers a rebuild.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- required keys:
--   schema_version  '3'
--   bank_id         sha1-derived id (section 6.2)
--   bank_root       absolute POSIX path of the bank root at build time
--   provider_key    EmbeddingProvider.key, e.g.
--                   'local:intfloat/multilingual-e5-large:1024'
--   embedding_dim   '1024'
--   created_at      ISO-8601 with offset
--   last_indexed_at ISO-8601 with offset (updated after every reconcile)

-- Change-state: file -> sha256. Lives in the DB, never in a side manifest.
CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,   -- POSIX relpath from the bank root
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    n_chunks   INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL       -- ISO-8601 with offset
);

-- One row per chunk (a section of a file).
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,   -- rowid, joins vec_chunks / fts_chunks
    chunk_uid   TEXT NOT NULL UNIQUE,  -- deterministic, machine-independent
    path        TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    heading     TEXT,
    content     TEXT NOT NULL,
    start_char  INTEGER NOT NULL,      -- offset in the source file
    end_char    INTEGER NOT NULL,      -- exclusive
    UNIQUE (path, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

-- Dense vectors. rowid == chunks.id.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    embedding float[1024]              -- config.EMBEDDING_DIM
);

-- Sparse / lexical fallback.
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(content);
```

PRAGMA лишаються як у v2: `journal_mode=WAL`, `synchronous=NORMAL`,
`busy_timeout=5000`.

### 3.2 Детермінований id чанка **[NEW]**

Інваріант «детермінований стабільний id чанка (однаковий на всіх машинах)» у
v2 **не реалізований буквально**: `chunks.id` — це autoincrement rowid, а
стабільність тримається на `UNIQUE (path, chunk_index)`. У v3 фіксуємо явний
ідентифікатор:

```
chunk_uid = sha1(f"{path}\x00{chunk_index}".encode("utf-8")).hexdigest()[:16]
```

* `path` — POSIX-relpath від кореня банку (роздільник завжди `/`).
* Ідентифікатор **не залежить від вмісту**: та сама секція того самого файлу
  має той самий uid на будь-якій машині й після переіндексації. Це те, що
  потрібно UI (підсвітити чанк), логам (послатись на видану секцію) і
  дедуплікації.
* `chunks.id` (rowid) лишається **внутрішнім** ключем звʼязку з `vec_chunks` /
  `fts_chunks` і назовні не витікає.

### 3.3 Публічні функції `store.py`

```python
def connect(db_path: Path) -> sqlite3.Connection
def init_meta(conn, *, bank_id: str, bank_root: str, provider_key: str,
              dim: int) -> None
def get_meta(conn) -> dict[str, str]
def needs_rebuild(conn, *, provider_key: str, dim: int) -> bool
def reset_index(conn) -> None            # drops all chunks/vectors/fts/files
def get_indexed_hashes(conn) -> dict[str, str]
def get_file_row(conn, path: str) -> sqlite3.Row | None
def insert_chunk(conn, *, chunk_uid: str, path: str, chunk_index: int,
                 heading: str, content: str, start_char: int, end_char: int,
                 embedding: list[float]) -> int
def set_file_hash(conn, *, path: str, sha256: str, size: int, mtime_ns: int,
                  n_chunks: int) -> None
def delete_file(conn, path: str) -> None     # prune: chunks + vec + fts + files
def get_vectors(conn, ids: list[int]) -> dict[int, list[float]]
def chunk_count(conn) -> int
def file_count(conn) -> int
def list_files(conn) -> list[sqlite3.Row]
def chunk_map(conn, path: str) -> list[sqlite3.Row]   # for the UI chunk-viz

def probe(db_path: Path) -> dict   # {'meta': dict, 'files': int|None, 'error': str|None}
```

`probe` — єдиний спосіб подивитися на індексний файл **не відкриваючи його для
роботи**: `mode=ro`, без sqlite-vec, без `journal_mode=WAL` (виставлення
journal mode — це запис, тобто діагностика лишала б свіжий `-wal` біля файлу,
який саме збирається назвати сміттям, і падала б на справді read-only базі).
Помилки **повертає, а не кидає**: викликач — діагностика (`doctor`,
`clean-orphans`), і нечитаний файл там факт для друку, а не привід обірвати
список. Застереження, яке варте окремого рядка: `mode=ro` ≠ «нічого не
чіпає» — SQLite при відкритті бази прибирає протухлий `-wal` як частину
recovery, тож розмір файлу треба міряти **до** `probe`, а не після. **[NEW]**

`needs_rebuild` повертає `True`, якщо `meta.schema_version != '3'`, або
`meta.provider_key` ≠ активний, або `meta.embedding_dim` ≠ `dim`. Реакція —
`reset_index` + повний реіндекс (це вирішує і «міграцію індексів» з
`Memory-implementation-v3.md` §6: нічого не конвертуємо).

`mtime_ns` і `size` зберігаються **для UI та діагностики**. Джерело істини про
зміну — **тільки sha256**; жодного «швидкого сканування за mtime» не вводимо
(зайвий клас багів на ФС з грубою точністю часу). **[NEW]**

### 3.4 Що потрібно від chunker (`chunker.py`) **[NEW]**

`start_char` / `end_char` потрібні для перевірки фази 6 — «межі чанків
збігаються з тим, що реально в індексі». Відтворювати їх у UI повторним
розбиттям — це другий шлях розбиття, тобто рівно те, чого уникаємо. Тому:

```python
@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    heading: str
    start: int   # character offset into the source text
    end: int     # exclusive


def split_markdown(text: str) -> list[Chunk]
```

Реалізується через `MarkdownSplitter.chunk_indices(text)`, яка вже віддає
`(offset, chunk)`. Правило розбиття (`CHUNK_CAPACITY = (200, 1200)`) **не
міняється** — інакше зміниться видача пошуку, а перевірка фази 0 вимагає «ті
самі релевантні результати».

---

## 4. Блок D — контракт індексатора

`src/index.py`. Розділяємо **обхід+diff** (без моделі, без запису) і
**ембед+запис** (з моделлю, з комітами).

```python
@dataclass(frozen=True)
class FileStat:
    path: str          # POSIX relpath from the bank root
    abs_path: Path
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class IndexPlan:
    added:   list[FileStat]
    changed: list[FileStat]
    removed: list[str]           # relpaths gone from disk

    @property
    def is_empty(self) -> bool


@dataclass(frozen=True)
class BatchResult:
    path: str
    batch: int            # 0-based
    batches: int
    chunks: int           # chunks written by this batch


@dataclass(frozen=True)
class ReconcileResult:
    files_indexed: int
    chunks_indexed: int
    files_pruned: int
    took_ms: float
    errors: list[tuple[str, str]]     # (path, message)


def scan_bank(root: Path, *, exclude: list[str]) -> dict[str, FileStat]:
    """Walk *.md under root, hash each. No DB, no model. Sorted =
    deterministic."""


def build_plan(conn, disk: dict[str, FileStat]) -> IndexPlan:
    """Pure hash-diff against the DB state. No DB writes."""


def index_file(
    conn,
    provider: EmbeddingProvider,
    fs: FileStat,
    *,
    batch_size: int = 16,
    start_batch: int = 0,
    on_batch: Callable[[BatchResult], None] | None = None,
    should_yield: Callable[[], bool] | None = None,
) -> int | None:
    """Index one file, committing after every batch.

    Returns the number of chunks written when the file is finished, or
    ``None`` when `should_yield()` asked us to step aside before the last
    batch (preemption, section 8.3). The resume point is **not** in the
    return value: it is `last BatchResult.batch + 1`, and the caller already
    has that result from `on_batch`."""


def prune(conn, removed: list[str]) -> int


def reconcile(
    conn,
    provider: EmbeddingProvider,
    root: Path,
    *,
    exclude: list[str],
    batch_size: int = 16,
    on_batch: Callable[[BatchResult], None] | None = None,
) -> ReconcileResult:
    """Full walk + diff + index changed + prune removed. Used by the bulk
    path and by reconcile-on-start."""
```

### 4.1 Одиниця роботи й межа коміту

* **Одиниця запису** — чанк; **одиниця відправки в провайдер і коміту** —
  батч ≤ `MNEMO_BATCH_SIZE` (типово 16). Малий файл = один батч
  (`Memory-implementation-v3.md` §4).
* Один батч = один виклик `provider.embed_passages(...)` + N `insert_chunk` +
  **один `conn.commit()`**.

### 4.2 Порядок операцій для одного файлу (crash-safety) **[NEW]**

Перевірка фази 1 вимагає: «вбити процес посеред індексації → уже зроблене
збережено, повторний запуск доробляє решту». Точний порядок, який це дає:

1. `delete_file(conn, path)` — знімаємо старі чанки **і рядок у `files`** →
   `commit()`. Після цього файл із погляду індексу «не проіндексований».
2. Для кожного батчу: ембед → `insert_chunk` × N → `commit()`.
3. Після останнього батчу: `set_file_hash(...)` → `commit()`.

Наслідки, які фіксуємо явно:

* Падіння між 2 і 3 → у базі частина чанків без рядка в `files`. Наступний
  прохід бачить файл як `added`, крок 1 змітає частковий залишок, файл
  переіндексовується з нуля. **Дублікатів не буває, індекс не стає стейлим.**
* «Уже зроблене збережено» тримається **на гранульованості файлу**: усі
  завершені файли лишаються, перерваний файл перероблюється цілком.
* Виняток — **preemption** (8.3): там `index_file` виходить *керовано*,
  повертаючи `None`, і `files` так само лишається незаписаним, тому зовнішня
  консистентність та сама. **Два стани, два різні типи** — щоб їх не можна
  було сплутати: `int` = «файл закінчено, стільки чанків записано», `None` =
  «не закінчено». Точку продовження бере не з `return`, а з останнього
  `BatchResult`, який колбек `on_batch` уже віддав викликачу:
  `start_batch = last.batch + 1`.

**Резюмування всередині файлу — свідомо відкладено (рішення тимліда).**
Причина не «дрібниця», а ціна: справжній resume вимагає **персистентного
прогресу** (скільки батчів цього файлу вже лежить у базі), який доведеться
тримати консистентним з диском при кожному падінні, ренеймі й зміні файлу
між спробами. Того, що дійсно важливо, ми досягаємо дешевше: детермінований
`chunk_uid` + `delete_file` перед вставкою означають, що **перероблений файл
фізично не може продублюватись**. Повернемось до цього лише якщо міряння
покаже реальні файли, де переробка коштує відчутно.

> **Що з цього має перевіряти tester (фаза 1).** Перевірка «вбити процес
> посеред індексації → уже зроблене збережено» виконується на **рівні файлу**,
> тому:
>
> * ганяти її треба на **багатофайловому корпусі** — інакше вона нічого не
>   доводить. Критерій: завершені до вбивства файли лишились у базі й **не**
>   переіндексовуються повторним запуском (їхній sha256 збігається);
> * файл, що був у польоті, після рестарту індексується **заново й повністю** —
>   це очікувана, задокументована поведінка, **не** дефект;
> * окремий випадок «один величезний файл» має бути **зафіксований у звіті**
>   (200+ чанків, убити на середині → після рестарту всі 200 чанків
>   перераховуються), щоб цю властивість знайшли в документі, а не відкрили
>   як сюрприз у продакшені;
> * і в обох випадках — жодного дубліката: `SELECT count(*)` по `chunks` для
>   цього шляху дорівнює кількості чанків файлу.

### 4.3 Помилки

`EmbeddingUnavailable` усередині `index_file` **не ковтається**: `index_file`
пропускає його нагору, воркер черги ловить, пише `index_events.result='error'`
і **не** позначає файл проіндексованим. Файл лишиться в `added/changed` і
доробиться наступним проходом — це і є мʼяка деградація NFR-10.

---

## 5. Блок H — контракт пошуку

`src/search.py` лишається **чистим**: він знає базу й провайдера, і **не знає**
ні черги, ні реєстру. Статус доклеює API (розділ 9.4) — блок H сам не має
джерела правди про чергу.

```python
@dataclass
class Hit:
    chunk_uid: str
    path: str                  # POSIX relpath inside the bank
    heading: str
    content: str
    score: float               # RRF score
    chunk_index: int           # -1 only for merged neighbour windows
    span: tuple[int, int] | None
    sim: float | None = None   # cosine vs query; set only on gated calls


def search(
    conn,
    query: str,
    *,
    qvec: list[float] | None = None,
    provider: EmbeddingProvider | None = None,
    top_k: int = TOP_K,
    path_prefix: str | None = None,
    gate: bool = False,
    min_sim: float | None = None,
    expand_window: int | None = None,
) -> list[Hit]
```

Зміни проти v2: зникли `scope` / `agent_name` / `root`; зʼявились
`path_prefix`, `conn`, `provider`. Пайплайн (vector kNN → FTS5/BM25 → RRF →
neighbour expansion → gate) **не змінюється** — перевірка фази 0 вимагає тих
самих результатів.

### 5.1 `path_prefix`

* Значення — POSIX-relpath від кореня банку, **без** початкового `/`
  (`"logs"`, `"logs/2026"`, `"topics/store.md"`). Порожній рядок і `"."`
  еквівалентні `None`.
* Збіг **по межі сегмента**: `chunks.path == prefix` **або**
  `chunks.path LIKE prefix || '/%'`. Тобто `"log"` **не** матчить `"logs/x.md"`.
  **[NEW]** — це прибирає найочевиднішу пастку простого `startswith`.
* Фільтр застосовується **після** kNN (`Memory-implementation-v3.md` §6 це
  прямо визнає й називає нюансом). Компенсація: за наявності `path_prefix`
  пул кандидатів росте з `max(top_k*4, 20)` до `min(max(top_k*40, 200), 500)`.
  **[NEW]** Число — стартова оцінка, перевірка фази 2 його не міряє; заміряти
  на фазі 6, коли зʼявиться реальний банк.
* Той самий фільтр застосовується і до FTS-ноги (в SQL, `WHERE`), і до
  neighbour-expansion (сусіди беруться лише з того самого файлу — вони й так
  у межах префікса).

### 5.2 Форма відповіді зі статусом

Це форма, яку віддає **API** (`/api/search`) і повертає `client.py`:

```python
Status = Literal["ready", "indexing", "empty"]

@dataclass
class SearchResult:
    bank_id: str
    query: str
    status: Status
    queued: int            # tasks pending for this bank, 0 when idle
    chunk_count: int       # chunks currently in this bank's index
    hits: list[Hit]
    took_ms: float
```

**Правило обчислення статусу [NEW]** (design §6 каже, що на першому білді
`empty` та `indexing` збігаються, але не каже, що віддавати):

```
if queued > 0 or worker_busy:      status = "indexing"
elif store.chunk_count(conn) == 0: status = "empty"
else:                              status = "ready"
```

`indexing` має **пріоритет** над `empty`. Причина — придатність до повтору:
`{"status":"empty"}` спонукає агента вирішити, що банк непотрібний, і більше
не питати; `{"status":"indexing","chunk_count":0}` каже «повернись пізніше».

Поля `queued` і `chunk_count` присутні **завжди**, тому жодна інформація не
губиться в жодному напрямку:

| Відповідь | Читається як |
|---|---|
| `status=indexing`, `chunk_count=0` | перший білд у процесі — порожньо, але вже меле |
| `status=indexing`, `chunk_count>0` | база є, свіжі зміни ще доїжджають |
| `status=empty`, `queued=0` | справді порожньо, і нічого не планується |
| `status=ready`, `hits=[]` | база є, збігів немає |

Останній рядок — та сама відмінність, якої вимагає рішення #11: `empty` (бази
немає) ≠ порожня видача (немає збігу).

---

## 6. Блок G — реєстр банків

### 6.1 Розташування й формат

Файл: `STATE_DIR / "banks.json"` (типово
`~/.claude/mnemo/state/banks.json`), override — `MNEMO_BANKS_FILE`.
Людиночитний, `indent=2`, `ensure_ascii=False`, правиться руками або через UI
(`Memory-implementation-v3.md` §6).

```json
{
  "version": 1,
  "banks": [
    {
      "id": "9f2a1c7b3e5d0846",
      "name": "mnemo",
      "root": "E:/work_projects/other/mnemo/.claude",
      "provider": "local",
      "enabled": true,
      "exclude": [".git/**", ".venv/**", "node_modules/**", "__pycache__/**"],
      "added_at": "2026-07-26T12:00:00+03:00",
      "token": "0f1e2d3c4b5a69780f1e2d3c4b5a69780f1e2d3c4b5a6978"
    }
  ]
}
```

| Поле | Тип | Обовʼязкове | Значення |
|---|---|---|---|
| `id` | string(16) | так | похідний від `root`, розділ 6.2 |
| `name` | string | так | **унікальна** людська назва — це публічна адреса банку (6.5); за замовчуванням імʼя теки-кореня, з суфіксом `-2`, `-3` при колізії |
| `root` | string | так | **абсолютний** шлях, POSIX-роздільники (`/`) навіть на Windows |
| `provider` | `"local" \| "api" \| null` | ні | `null` → провайдер сервісу (`MNEMO_PROVIDER`) |
| `enabled` | bool | ні (типово `true`) | `false` → не стежимо, не індексуємо, у пошуку віддаємо 404 |
| `exclude` | string[] | ні | glob-патерни відносно кореня; типове значення — у таблиці вище **[NEW]** |
| `added_at` | string | так | ISO-8601 з офсетом |
| `token` | string(48) | так | **[NEW]** власний токен банку — 48 hex, той самий генератор, що й `api.token`. Відкриває MCP-обличчя саме цього банку (9.1). Карбується при `add`; порожній лише у банку, зареєстрованого до появи токенів і ще не мігрованого |

Невідомі поля **зберігаються as-is** при перезаписі (щоб ручна примітка
користувача не зникала). **[NEW]**

**Наслідок появи `token`: `banks.json` — файл із секретами. [NEW]** Пишеться
з `chmod 0600` (best-effort, як `api.token`; на Windows реальний захист —
ACL профілю). API токенів **не показує у списку банків** — `BankInfo` його не
містить: список банків малюється в кабінеті й вставляється в тікети, а секрет,
що їздить із кожним лістингом, витікає випадково. Токен беруть окремим
запитом на один банк (9.5).

**API модуля (доповнення до 6.3):**

```python
ensure_tokens() -> int          # міграція: докарбувати відсутні; скільки додано
token_for(bank_id) -> str       # токен банку; карбує й зберігає, якщо його немає
regenerate_token(bank_id) -> str  # ротація; старий перестає працювати негайно
```

`update()` **переносить** токен, а не перевипускає: перейменування банку не
має тихо ламати кожен `.mcp.json`, що на нього вказує. Ротація — тільки
`regenerate_token`, і після неї wiring треба перевипустити (`mnemo init`).

### 6.2 Похідний `bank_id` **[NEW]**

```
canonical = Path(root).expanduser().resolve().as_posix()
if os.name == "nt":
    canonical = canonical.lower()      # NTFS is case-insensitive
bank_id = sha1(canonical.encode("utf-8")).hexdigest()[:16]
```

* Схема `sha1(root)[:16]` — та сама, що у v2 (`Memory-implementation-v3.md` §6:
  «ключ `sha1(root)` лишається, але корінь тепер корінь банку»).
* Дві нові деталі: **`as_posix()`** (щоб `E:\x` і `E:/x` не давали різні бази)
  і **`.lower()` на Windows** (щоб `E:/Work` і `E:/work` не давали дві бази на
  одну теку). На POSIX регістр значущий — не чіпаємо.
* Файл індексу: `STATE_DIR / f"{bank_id}.db"`.

### 6.3 API модуля

```python
class BankNotFound(LookupError): ...
class BankExists(ValueError): ...
class AmbiguousBankRef(LookupError): ...

@dataclass(frozen=True)
class Bank:
    id: str
    name: str
    root: Path
    provider: str | None
    enabled: bool
    exclude: list[str]
    added_at: str

    @property
    def db_path(self) -> Path            # STATE_DIR / f"{id}.db"
    @property
    def exists(self) -> bool             # root is a real directory
    @property
    def is_git(self) -> bool             # root sits inside a git work tree


def load(*, force: bool = False) -> list[Bank]
    """Cached; reloads automatically when banks.json mtime changed, so a
    hand edit is picked up without restarting the service."""

def list_banks() -> list[Bank]
def get(bank_id: str) -> Bank                    # raises BankNotFound
def resolve(ref: str) -> Bank                    # see 6.4
def add(root: Path | str, *, name: str | None = None,
        provider: str | None = None) -> Bank     # raises BankExists
def remove(bank_id: str, *, drop_index: bool = True) -> None
def update(bank_id: str, **fields) -> Bank       # raises BankExists on a name clash
def unique_name(candidate: str) -> str           # 'notes' -> 'notes-2' -> 'notes-3'
def save(banks: list[Bank]) -> None              # atomic: tmp + os.replace

@dataclass(frozen=True)
class OrphanIndex:
    path: Path;  id: str;  size: int            # .db + its -wal / -shm siblings
    root: str | None;  root_exists: bool        # from meta.bank_root, None pre-v3
    schema: str | None;  files: int | None
    last_indexed: str | None;  error: str | None

def orphan_indexes() -> list[OrphanIndex]        # raises if banks.json is unreadable
def delete_index(index_id: str) -> tuple[int, list[Path]]   # (removed, locked)
```

**Осиротілі індекси (design #25).** `orphan_indexes()` — це `state/*.db` мінус
`service.db` мінус id живих банків. Реєстр читається **першим, і його помилка
летить далі**: вважати нечитаний `banks.json` за «банків немає» означало б
назвати орфаном кожен живий індекс. `delete_index` перечитує реєстр **у момент
видалення** — між показом списку й підтвердженням банк міг зʼявитися (кабінет,
інша сесія, `init`), і застарілий список не має його зітерти. `service.db`
виключено **за іменем**, не за вмістом. **[NEW]**

### 6.4 Семантика `resolve(ref)` **[NEW]**

Один рядок `ref` приймає три форми, у цьому порядку:

1. **`bank_id`** — точний збіг 16-символьного hex.
2. **`name`** — точний збіг імені. Імена унікальні (6.5), тож форма
   однозначна.
3. **Шлях** — **тільки абсолютний** (або `~`-шлях). Шукає в **обидва боки**,
   у цьому порядку: **[NEW]**
   * точний збіг кореня;
   * **вгору** — найглибший банк, чий корінь є *предком* шляху (так `cwd`
     сесії резолвиться у свій банк, і вкладений банк виграє в того, що його
     охоплює);
   * **вниз** — банк, чий корінь лежить *під* шляхом. Без цього канонічна
     розкладка била сама себе: памʼять живе в `<проєкт>/.claude/memory`, тож
     `mnemo search` **з кореня проєкту** — найочевидніше місце запуску — не
     знаходив нічого й виходив з кодом 1, тоді як та сама команда двома
     теками нижче працювала. Кілька банків під шляхом → `AmbiguousBankRef`,
     а не вгадування.

   Нічого не знайдено → `BankNotFound`.

**Відносний шлях `resolve` не інтерпретує — і це контракт, не недогляд.**
Функція виконується **всередині сервісу**, чий `cwd` не має нічого спільного
з `cwd` того, хто питає; приєднати відносний ref до свого `cwd` означало б
впевнено відповісти про теку, якої користувач не називав. Абсолютизація —
робота клієнта, зроблена в одному місці: `cli._bank_ref`. Там же голе слово
**лишається іменем** — інакше опечатка (`odin-crn`) перетворилася б на
`<cwd>/odin-crn` і з надр іншого банку зарезолвилась би в **той** банк.

`AmbiguousBankRef` має тепер два джерела. Захисне: реєстр — людиночитний
файл, і руками в нього можна вписати два однакові імені (через `add` /
`update` таке не проходить). І штатне: шлях, під яким лежить кілька банків —
тека з проєктами. Обидва дають `bank_ambiguous`, 409, з переліком імен.

### 6.5 Унікальність імені — імʼя і є адресою банку **[NEW]**

**Рішення тимліда.** Банк адресується **назвою**, а не `bank_id`, скрізь, де
адреса потрапляє в git (насамперед `.mcp.json`, розділ 10.4). Причина:
`bank_id = sha1(root)[:16]` — похідна від **шляху на конкретній машині**, тож
у git-трекованому файлі вона нічого не варта після `git clone` в інше місце.
Назва стабільна між машинами й чекаутами.

Щоб назва була надійною адресою, реєстр **примусово тримає її унікальною**:

* `add()` без `name` бере імʼя теки-кореня; `add()` з `name` бере його як є.
* У обох випадках назва проходить `unique_name()`: збіг з наявною → суфікс
  `-2`, далі `-3` і т.д. Повернений `Bank.name` — це вже фінальна, зайнята
  назва (викликач має її прочитати, а не припускати).
* `update(bank_id, name=...)` на вже зайняте імʼя → `BankExists`. Тут
  **не** авто-суфіксуємо: перейменування — навмисна дія, тиха підміна імені
  зламала б `.mcp.json`, який на нього посилається.
* Порівняння імен — регістронезалежне й після `strip()`, щоб `Notes` і
  `notes ` не читались як дві різні адреси.

Наслідок для UI: перейменування банку — операція, яка може відмовити; форму
треба будувати з обробкою 409.

---

## 7. Блок I — журнал сервісу

`src/servicelog.py`, файл `STATE_DIR / "service.db"`. Пише **тільки бекенд**,
одна конекція, WAL.

### 7.1 CREATE TABLE

```sql
CREATE TABLE IF NOT EXISTS query_events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,      -- ISO-8601 with offset, human-readable
    ts_epoch    REAL NOT NULL,      -- sort/filter/retention key
    bank_id     TEXT NOT NULL,
    face        TEXT NOT NULL,      -- 'mcp' | 'cli' | 'http' | 'hook' | 'ui'
    query       TEXT NOT NULL,
    path_prefix TEXT,
    status      TEXT NOT NULL,      -- 'ready' | 'indexing' | 'empty'
    n_hits      INTEGER NOT NULL,
    took_ms     REAL NOT NULL,
    hits_json   TEXT NOT NULL       -- see 7.2
);
CREATE INDEX IF NOT EXISTS idx_qe_bank_ts ON query_events(bank_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_qe_ts      ON query_events(ts_epoch DESC);

CREATE TABLE IF NOT EXISTS index_events (
    id             INTEGER PRIMARY KEY,
    ts             TEXT NOT NULL,
    ts_epoch       REAL NOT NULL,
    bank_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,   -- 'file' | 'bulk' | 'prune' | 'rebuild'
    trigger        TEXT NOT NULL,   -- 'watcher'|'api'|'startup'|'cli'|'mcp'|'ui'
    path           TEXT,            -- NULL for bulk/rebuild
    result         TEXT NOT NULL,   -- 'ok' | 'error' | 'skipped'
    files_indexed  INTEGER NOT NULL DEFAULT 0,
    chunks_indexed INTEGER NOT NULL DEFAULT 0,
    files_pruned   INTEGER NOT NULL DEFAULT 0,
    took_ms        REAL,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ie_bank_ts ON index_events(bank_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_ie_ts      ON index_events(ts_epoch DESC);
```

Обидва індекси на кожну таблицю потрібні саме тому, що кабінет фільтрує
**за банком** і **загально по всіх банках** (design §6).

### 7.2 `hits_json`

Масив рівно того, що пішло у відповідь — «завжди видно, що прийшло на запит і
що пішло у відповідь» (design §6):

```json
[{"chunk_uid":"a1b2…","path":"logs/2026-07-26.md","heading":"Queue",
  "chunk_index":3,"score":0.0324,"sim":0.861}]
```

`sim` — `null`, якщо виклик не був gated.

### 7.3 API модуля

```python
def connect() -> sqlite3.Connection
def log_query(*, bank_id: str, face: str, query: str, path_prefix: str | None,
              status: str, hits: list[Hit], took_ms: float) -> None
def log_index(*, bank_id: str, kind: str, trigger: str, path: str | None,
              result: str, files_indexed: int = 0, chunks_indexed: int = 0,
              files_pruned: int = 0, took_ms: float | None = None,
              error: str | None = None) -> None
def read_queries(*, bank_id: str | None = None, since: float | None = None,
                 until: float | None = None, limit: int = 200,
                 offset: int = 0) -> list[dict]
def read_index(*, bank_id: str | None = None, since: float | None = None,
               until: float | None = None, kind: str | None = None,
               limit: int = 200, offset: int = 0) -> list[dict]
def count(table: str, **filters) -> int
def prune(retention_days: int, max_rows: int | None = None) -> tuple[int, int]
```

`log_*` **ніколи не піднімає виняток** — телеметрія не має права зламати ні
пошук, ні індексацію (правило успадковане з `inject_log.py`).

### 7.4 Retention

* `MNEMO_LOG_RETENTION_DAYS`, типово **30** (NFR-8).
* `prune` викликається **на старті бекенда** і далі **кожні 6 год** **[NEW]**
  (NFR-8 каже «на старті + періодично», періоду не називає).
* Backstop за рядками: `MNEMO_LOG_MAX_ROWS`, типово **200000** на таблицю;
  `0` вимикає. **[NEW]**
* Після `prune` — `PRAGMA incremental_vacuum` не робимо; `VACUUM` — раз на
  добу, якщо видалено > 10 000 рядків. **[NEW]**

### 7.5 Доля `inject_log.py` **[NEW]**

Модуль і його JSONL під `state/logs/` видаляються у фазі 2. Замість них
auto-inject хук пише **звичайну подію `query_events` з `face='hook'`**. Поля,
яких у новій схемі немає (`embed_ms`, `search_ms`, `status='skipped_*'`),
згортаються так: `status` лишається трьома значеннями пошуку, а «пропущено» —
це просто відсутність події (хук вийшов, нічого не питавши). Втрачаємо
калібрувальну телеметрію `MIN_SIM`; це прийнятно, бо `sim` кожного хіта тепер
пишеться в `hits_json`.

Команда `mnemo projects`, що читала ці JSONL, зникає — її заміняє
`mnemo banks list`.

---

## 8. Блок E — черга й воркер

`src/workqueue.py`.

### 8.1 Форма задачі

```python
class Priority(IntEnum):
    HIGH   = 0     # single/incremental edit — jumps the queue
    NORMAL = 1     # explicit per-file reindex from UI/CLI
    LOW    = 2     # bulk: new bank, full reindex, startup reconcile


TaskKind = Literal["file", "bulk", "prune", "rebuild"]


@dataclass(frozen=True)
class Task:
    id: str                  # uuid4 hex[:12]
    bank_id: str
    kind: TaskKind
    priority: Priority
    trigger: str             # 'watcher'|'api'|'startup'|'cli'|'mcp'|'ui'
    path: str | None = None  # relpath; required for kind='file'
    start_batch: int = 0     # >0 only for a resumed, preempted file
    enqueued_at: float = 0.0
    seq: int = 0             # monotonic; FIFO tiebreaker within a priority
```

Ключ у `queue.PriorityQueue` — кортеж `(priority, seq)`. `seq` монотонний і
глобальний → всередині одного пріоритету завжди FIFO, і `Task` ніколи не
порівнюється сам із собою.

### 8.2 Семантика видів задач

| `kind` | Що робить | Типовий пріоритет |
|---|---|---|
| `file` | індексує **один** файл (`index_file`) | `HIGH` від watcher, `NORMAL` від кнопки «переіндексувати файл» |
| `bulk` | **не ембедить сам**: `scan_bank` + `build_plan`, далі кладе в чергу один `file` на кожен змінений файл (`LOW`) + один `prune` | `LOW` |
| `prune` | знімає з індексу перелічені зниклі шляхи | той самий, що в породжуючого `bulk`; від watcher — `HIGH` |
| `rebuild` | `reset_index` + `bulk` (зміна провайдера / схеми) | `LOW` |

Такий розклад — це і є «почанкова гранульованість» рішення #10: у черзі ніколи
не лежить одна монолітна годинна задача, лежать окремі файли.

### 8.3 Що конкретно означає «поодинока правка обганяє масовий реіндекс» **[NEW]**

Три механізми разом:

1. **Пріоритет.** `HIGH`-задача стає перед усіма `LOW` у черзі. Це покриває
   випадок «bulk ще не почався або вже між файлами».
2. **Декомпозиція `bulk`.** Bulk не займає воркер на годину — він займає його
   на час `scan+diff` і розчиняється в окремі `file`-задачі. Найдовше, що
   блокує чергу, — **один файл**.
3. **Витіснення між батчами.** `index_file` після кожного закомміченого батчу
   викликає `should_yield()`. Воркер повертає `True`, якщо
   `current.priority == LOW` **і** в черзі чекає `HIGH`. Тоді `index_file`
   повертає номер наступного батчу, воркер кладе назад
   `Task(kind="file", start_batch=n, priority=LOW, seq=<новий>)` і бере
   `HIGH`. Максимальна затримка термінової правки — **один батч (~16 чанків)**.

Правила відновлення перерваного файлу:

* `start_batch > 0` → крок 1 з розділу 4.2 (`delete_file`) **не повторюється**.
* Перед продовженням звіряється `sha256` файлу з тим, що був на початку. Змінився
  → задача перезапускається з `start_batch=0` (з `delete_file`).
* Файл зник → задача завершується як `skipped`, шлях іде в `prune`.

**Дедуплікація.** Enqueue `file`-задачі для `(bank_id, path)`, яка вже
чекає в черзі, **не додає другу**: якщо новий пріоритет вищий — наявна
підвищується (видаляється й кладеться заново з новим `seq`), інакше ігнорується.

**Прапорець.** `MNEMO_QUEUE_PRIORITY`, типово `1`. `0` → усе стає `NORMAL`,
`should_yield()` завжди `False`, чиста FIFO (рішення #10: «за потреби
пріоритезацію можна вимкнути»).

### 8.4 API модуля

```python
@dataclass
class QueueSnapshot:
    depth: int
    high: int
    normal: int
    low: int
    current: Task | None
    current_batch: int
    current_batches: int


def start(*, workers: int = 1, on_event: Callable[[dict], None]) -> None
def stop(timeout: float = 10.0) -> None
def enqueue(task: Task) -> str                  # returns task.id
def enqueue_file(bank_id, path, *, priority, trigger) -> str
def enqueue_bulk(bank_id, *, trigger, rebuild: bool = False) -> str
def depth(bank_id: str | None = None) -> int
def busy(bank_id: str | None = None) -> bool
def snapshot() -> QueueSnapshot
```

`on_event` — це міст до WebSocket: воркер віддає туди готові конверти з
розділу 9.7. Черга **не імпортує** ні `api`, ні `servicelog` — вона їх
викликає через колбек. `MNEMO_WORKERS` типово `1` (рішення: «БД пише тільки
бекенд», один писака).

---

## 9. Блок J — HTTP API

`src/api.py`, FastAPI + uvicorn.

### 9.1 Адреса, монтування, автентифікація

| Що | Значення |
|---|---|
| Bind | `MNEMO_API_HOST`, типово `127.0.0.1` |
| Порт | `MNEMO_API_PORT`, типово **8918** **[NEW]** (8917 уже зайнятий embed-резидентом) |
| Дані (**приватне**) | префікс `/api` — канал кабінет↔бекенд, `include_in_schema=False` **[NEW]** |
| MCP (проєктне) | `/mcp` — **без сегмента банку**, банк із токена (10.3) |
| MCP (адмінське) | `/mcp-admin` — **без сегмента банку** (10.5) **[NEW]** |
| Дзеркало тулів (**зовнішнє**) | `/mcp-tools/<tool_name>` (9.8) **[NEW]** |
| WebSocket | `/ws` |
| Статика UI | `/ui` |
| Liveness | `/health` |
| OpenAPI | `/docs`, `/openapi.json` — показують **лише** `/health` і `/mcp-tools/*` **[NEW]** |

**Токени — два різні, і різниця принципова. [NEW]**

* **Сервісний токен** — `STATE_DIR / "api.token"`, 48 hex-символів,
  створюється при першому старті, `chmod 0600` (best-effort), як `embed.token`.
  Це ширший креденшл: він належить кабінету, CLI й адмінському обличчю.
* **Токен банку** — 48 hex, той самий генератор, живе полем `token` у
  `banks.json` поруч із банком (6.1). Карбується при реєстрації банку; банки,
  зареєстровані до появи токенів, отримують його **міграцією на старті**
  (`registry.ensure_tokens`) — це *додавання поля*, а не перезапис документа:
  все інше, включно з незнайомими полями, переживає її незмінним.

**Матриця доступу.** Кожна поверхня бере рівно **один** вид креденшла —
поверхні, яка приймає два, більше немає:

| Поверхня | Відкривається |
|---|---|
| `/mcp` | **тільки токеном банку** — сервісним **ні** |
| `/mcp-admin` | **тільки** сервісним — токеном банку ніколи |
| `/mcp-tools/*` | сервісним (він зберігає явний параметр `bank`) |
| `/api/*` | сервісним |

**Чому сервісний токен не відкриває `/mcp`.** Токен *і є* адресою (10.3), тож
сервісному тут нема до якого банку резолвитись — прийняти його означало б
вгадувати, який мали на увазі. Замість цього `401` з дієвим текстом: що тут
потрібен токен банку, і що сервісний належить на `/mcp-admin` або
`/mcp-tools`. Найімовірніший власник відхиленого тут токена — це людина із
сервісним, цілком валідним на трьох інших поверхнях, і «missing or invalid
API token» відправило б її шукати неіснуючу проблему з креденшлом.

Токен банку A звертається до банку A і ні до чого іншого; на `/mcp-admin`,
`/mcp-tools` і `/api` він — **401**. Це головна властивість, і саме її
перевіряють найжорсткіше (`tests/test_mcp.py`).

**Сегмент шляху не приймається й не потрібен.** `/mcp/<будь-що>` → **400** з
текстом, що банк береться з токена, плюс порада `mnemo init --migrate`.
Перевірка стоїть **перед** автентифікацією: найчастіший спосіб сюди
потрапити — конфіг попереднього покоління, чий токен **валідний**, і сказати
такому «unauthorized» означало б відправити його шукати проблему з
креденшлом, якої немає.

**Що це таке чесно: найменша достатня привілея, а не стіна.** Хто має
сервісний токен — відкриє будь-який банк; хто може прочитати `banks.json` —
прочитає всі токени в ньому. Купується цим одне: віддати колезі `.mcp.env`
одного проєкту не означає віддати кожен інший банк на машині. `banks.json`
тепер файл із секретами й отримує той самий `chmod 0600`.

Порядок перевірки в middleware: **`/mcp-admin` читається раніше за `/mcp`**.
Він починається з `/mcp`, і зворотний порядок віддав би адмінське обличчя
токену банку — рівно та поломка, від якої цей порядок і захищає.

* HTTP: `Authorization: Bearer <token>`; альтернативний заголовок
  `X-Mnemo-Token: <token>`. **[NEW]**
* WebSocket: браузер не вміє ставити заголовки на WS → **`/ws?token=<token>`**.
  **[NEW]**
* **Без токена:** `/health` (потрібен `service_ctl`, щоб перевірити живість до
  того, як він десь візьме токен) і **статика** `/ui/*` (це асети, не дані).
  Усе під `/api`, `/mcp` і `/mcp-tools` — під токеном. **[NEW]**
* `/mcp-tools/*` приймає токен ще й через **`?token=`**, як `/mcp` — це поверхня
  для ручного тику, і `curl` без заголовка мусить працювати. Ціна відома
  (секрет лишається в історії шелу й у логах проксі) і прийнята **лише тут**:
  для `/api` цього немає. **[NEW]**
* **Swagger «Try it out» мусить працювати:** у схемі оголошено
  `securitySchemes.bearerAuth` (HTTP bearer), тож на сторінці `/docs` є кнопка
  **Authorize**. Без цього авторизація живе тільки в middleware, у схему не
  потрапляє, і кожен тик у свагері віддає `401` без жодного способу це
  виправити. **[NEW]**
* UI отримує токен так: `mnemo ui` читає файл і відкриває
  `http://127.0.0.1:8918/ui/?token=…`; сторінка перекладає його в
  `sessionStorage` і далі шле заголовком. **[NEW]**

Невірний / відсутній токен → `401` з тілом помилки (розділ 9.2).

### 9.2 Форма помилки **[NEW]**

Єдина для всіх ендпоінтів:

```json
{"error": {"code": "bank_not_found",
           "message": "no bank matches 'notes'",
           "detail": {"ref": "notes"}}}
```

| `code` | HTTP | Коли |
|---|---|---|
| `unauthorized` | 401 | немає / невірний токен |
| `bad_request` | 400 | семантично невірний аргумент |
| `validation_error` | 422 | тіло не проходить схему (FastAPI) |
| `bank_not_found` | 404 | `resolve` не знайшов банк |
| `bank_ambiguous` | 409 | `ref` збігся з кількома банками — лише при ручному редагуванні реєстру (6.5) |
| `bank_exists` | 409 | `POST /api/banks` на вже зареєстрований корінь, або перейменування на зайняту назву |
| `root_not_found` | 400 | корінь не існує або не тека |
| `path_outside_bank` | 400 | `path` виходить за корінь банку |
| `file_not_found` | 404 | немає такого файлу в банку |
| `embed_unavailable` | 503 | провайдер не дає векторів |
| `internal` | 500 | усе інше |

### 9.3 `GET /health` — без токена

```json
{"ok": true, "version": "3.0.0", "pid": 12345, "port": 8918,
 "uptime_s": 3601.4, "banks": 3, "queue_depth": 0,
 "embed": {"provider": "local", "reachable": true,
           "host": "127.0.0.1", "port": 8917}}
```

### 9.4 `POST /api/search`

```json
{"bank": "9f2a1c7b3e5d0846", "query": "як влаштована черга",
 "top_k": 5, "path_prefix": null, "expand_window": null, "face": "cli"}
```

* `bank` — **id, назва або абсолютний шлях**; резолвиться через
  `registry.resolve` (розділ 6.4). Це дозволяє хуку передати просто `cwd`.
  **[NEW]**
* `top_k` — 1..50, типово `TOP_K` (5).
* `face` — необовʼязково, типово `"http"`; іде в `query_events.face`.

Відповідь `200`:

```json
{"bank_id": "9f2a1c7b3e5d0846", "bank_name": "mnemo",
 "query": "як влаштована черга",
 "status": "ready", "queued": 0, "chunk_count": 318, "took_ms": 41.2,
 "hits": [
   {"chunk_uid": "a1b2c3d4e5f60718",
    "path": "topics/queue.md", "heading": "Priority queue",
    "chunk_index": 2, "span": [1, 3],
    "score": 0.03251, "sim": null,
    "content": "…"}
 ]}
```

`status` рахується за правилом 5.2.

**Пошук не повертає 503 ніколи.** Ні індексація, ні недоступний провайдер не
перетворюються на помилку: **[NEW]**

* індексація → `200`, `status="indexing"`, віддаємо те, що вже є (рішення #11);
* провайдер недоступний → `200`, `hits: []`, `status` як є, плюс поле
  `"degraded": "embed_unavailable"`. Це NFR-10 — «деградує мʼяко, а не падає»;
  код `embed_unavailable` (503) лишається за ендпоінтами **запису**
  (`/api/reindex`), де без векторів справді нічого не зробити.

Кожен виклик пише один рядок у `query_events`.

### 9.5 Решта ендпоінтів

**`GET /api/banks`**

```json
{"banks": [ /* BankInfo */ ]}
```

`BankInfo` — єдина форма банку, яку віддає API (її ж шле WS-подія
`bank_status`):

```json
{"id": "9f2a1c7b3e5d0846", "name": "mnemo",
 "root": "E:/work_projects/other/mnemo/.claude",
 "provider": "local", "enabled": true,
 "exists": true, "git": true,
 "files": 42, "chunks": 318, "db_bytes": 4718592,
 "last_indexed": "2026-07-26T12:31:07+03:00",
 "status": "ready", "queued": 0, "indexing": false,
 "last_error": null}
```

**`POST /api/banks`** — тіло `{"root": "...", "name": null, "provider": null}`
→ `201` + `BankInfo`. Побічний ефект: ставить `bulk`-задачу з `trigger="api"`,
`priority=LOW`, і починає стежити (фаза 3).

**`GET /api/banks/{bank_id}/token`** **[NEW]** →

```json
{"bank_id": "9f2a1c7b3e5d0846", "name": "mnemo", "token": "3f1a…54ba"}
```

**`POST /api/banks/{bank_id}/token`** **[NEW]** — ротація, та сама форма
відповіді з новим значенням. Старий токен перестає працювати негайно, тож
кожен `.mcp.json`, що вказує на цей банк, треба перевипустити (`mnemo init`).

Обидва — під `/api`, тобто **сервісний токен** і `include_in_schema=False`.
Токен свідомо винесено з `BankInfo` в окремий ендпоінт на один банк: його
показує рівно та в'юха кабінету, яка для цього й існує, а не кожен лістинг.

**`DELETE /api/banks/{bank_id}?drop_index=true`** → `200 {"ok": true}`.
`drop_index=true` (типово) видаляє `<bank_id>.db`. `.md` **ніколи** не чіпаються.

**`GET /api/banks/{bank_id}`** → `BankInfo`.

**`GET /api/fs/dirs?path=…`** — перелік **підтек однієї теки**, для вибору
кореня банку в кабінеті. Без `path` — домівка користувача. **[NEW]**

```json
{"path": "E:/work_projects/other/mnemo",
 "display": "E:\\work_projects\\other\\mnemo",
 "parent": "E:/work_projects/other",
 "home": "C:/Users/dima",
 "roots": [{"name": "C:", "path": "C:/"}, {"name": "E:", "path": "E:/"}],
 "registered": null,
 "md": 42, "md_capped": false,
 "entries": [{"name": ".claude", "path": "E:/…/mnemo/.claude",
              "registered": "mnemo"}],
 "truncated": false}
```

* **Чому це взагалі існує.** Сторінка **не може** дізнатися шлях до вибраної
  теки: `webkitdirectory` віддає лише відносні імена, а `showDirectoryPicker()`
  повертає handle і шлях приховує **навмисно**. Тому обхід ФС робить бекенд —
  інакше «додати банк» у кабінеті неможливе в принципі.
* `path` — абсолютний (`~` розкривається); відносний → `bad_request`.
* `entries` — **лише теки**, відсортовані регістронезалежно; symlink/junction
  проходяться (рекурсії немає, тому цикл нічого не коштує). Імена файлів
  **не віддаються ніколи**, вміст — тим паче.
* `registered` (у корені відповіді й у кожному `entry`) — назва банку, якщо ця
  тека вже зареєстрована. Кабінет так гасить кнопку замість того, щоб ловити
  `bank_exists` навмання.
* `md` — `.md` **разом із підтеками**, з тими самими `DEFAULT_EXCLUDE`, що й у
  індексатора (інакше число обіцяло б файли, яких в індексі не буде).
  `md_capped: true` = «щонайменше стільки»: обхід має бюджет **0.4 с**
  (`_MD_SCAN_SECONDS`) і стелю 20000 тек. Бюджет саме за часом — клік у
  браузері тек мусить залишатися кліком, а тека проєктів пробиває будь-яку
  адекватну стелю за кількістю тек і повертає число, помилкове на два порядки.
* `truncated: true` — тек більше за `_FS_ENTRY_LIMIT` (500); решта досяжна
  вставленим шляхом, не кліками.
* Помилки: `bad_request` з розділеними формулюваннями «немає такої теки» /
  «це файл, а не тека» / «нема доступу» — це різні помилки користувача. Нових
  кодів помилок ендпоінт не вводить.
* Безпека: ендпоінт під тим самим Bearer-токеном на loopback, але він **ширший
  за решту API** — дозволяє перелічити назви тек будь-де, куди має доступ
  користувач сервісу (решта API бачить лише зареєстровані банки). Це свідомо:
  без цього вибір теки в кабінеті неможливий. Токен не cookie, тому сторонній
  сайт ним скористатися не може.

**`POST /api/reindex`**

```json
{"bank": "mnemo", "path": null, "full": false}
```

| Комбінація | Дія | Пріоритет |
|---|---|---|
| `path` задано | `file`-задача на цей файл | `NORMAL` |
| `path=null, full=false` | `bulk` (hash-diff реконсиляція) | `LOW` |
| `path=null, full=true` | `rebuild` (`reset_index` + bulk) | `LOW` |

Відповідь `202`: `{"ok": true, "task_ids": ["7c1e…"], "queued": 5}`.

**`GET /api/tree?bank=<ref>&links=false&depth=0`**

```json
{"bank_id": "9f2a…", "root": "E:/…/.claude", "files": 42, "dirs": 6,
 "tree": {"name": "", "type": "dir", "path": "", "children": [
   {"name": "logs", "type": "dir", "path": "logs", "children": [ … ]},
   {"name": "MEMORY.md", "type": "file", "path": "MEMORY.md",
    "size": 1843, "indexed": true, "chunks": 3,
    "headings": ["Memory Index", "Quick facts"],
    "links": ["topics/store.md"]}
 ]}}
```

* Діти сортуються: спершу теки, потім файли, кожна група — за іменем.
* `headings` беруться з `chunks.heading` індексу (безкоштовно) — саме тому
  дерево показує **заголовки**, як вимагає design §2.
* `links` (внутрішні `.md`-посилання) рахуються лише при `links=true` —
  це читання всіх файлів, тож за замовчуванням вимкнено. **[NEW]**
* `depth=0` — без обмеження.

**`GET /api/file?bank=<ref>&path=<relpath>`** **[NEW]** — цього ендпоінта немає
в списку `Memory-implementation-v3.md` §5, але без нього не існує FR-7
(«перегляд `.md`» + chunk-viz):

```json
{"bank_id": "9f2a…", "path": "topics/queue.md",
 "size": 4210, "sha256": "…", "indexed": true,
 "text": "# Priority queue\n…",
 "chunks": [{"chunk_uid": "a1b2…", "chunk_index": 0,
             "heading": "Priority queue", "start_char": 0, "end_char": 1180}]}
```

* Тільки `*.md` у межах кореня банку; вихід за корінь → `path_outside_bank`.
* Ліміт розміру `MNEMO_FILE_MAX_BYTES`, типово 2 MiB; більше → `400`.
* `chunks` — рівно те, що в індексі. Це і є контракт перевірки фази 6.

**`GET /api/status`**

```json
{"service": {"version": "3.0.0", "pid": 12345, "port": 8918,
             "started_at": "2026-07-26T09:00:00+03:00", "uptime_s": 3601.4,
             "provider": "local", "priority_enabled": true,
             "embed": {"reachable": true, "host": "127.0.0.1", "port": 8917}},
 "queue": {"depth": 3, "high": 1, "normal": 0, "low": 2,
           "current": {"task_id": "7c1e…", "bank_id": "9f2a…",
                       "kind": "file", "path": "logs/2026-07-26.md",
                       "batch": 2, "batches": 7,
                       "started_at": "2026-07-26T12:31:00+03:00"}},
 "banks": [ /* BankInfo */ ]}
```

**`GET /api/logs?kind=query&bank=<ref>&since=&until=&limit=200&offset=0`**

* `kind` — `query` | `index` (обовʼязково).
* `since` / `until` — ISO-8601 або epoch-секунди.
* `bank` відсутній → **усі банки** (design §6 вимагає обидва режими).

```json
{"kind": "query", "total": 1284, "limit": 200, "offset": 0,
 "events": [{"id": 1284, "ts": "2026-07-26T12:31:07+03:00",
             "ts_epoch": 1785058267.412,
             "bank_id": "9f2a…", "face": "mcp",
             "query": "як влаштована черга", "path_prefix": null,
             "status": "ready", "n_hits": 5, "took_ms": 41.2,
             "hits": [ /* hits_json, розпакований */ ]}]}
```

* Рядок несе **обидві** позначки часу: `ts` (ISO-8601, для показу) і
  `ts_epoch` (той самий стовпець, що в схемі 7.1 — числовий ключ сортування
  й фільтрації). Клієнт сортує за `ts_epoch`, а не парсить рядок. Це
  стосується обох `kind`. **[NEW]**

### 9.6 Порядок старту бекенда

1. Прочитати / створити `api.token`.
2. `servicelog.connect()` + `prune(retention)`.
3. `registry.load()`.
4. Для кожного банку: `store.connect` → `needs_rebuild`? → `rebuild` : `bulk`
   (**reconcile-on-start**, NFR-10 — «нічого не губиться, поки сервіс спав»).
5. `workqueue.start(workers=…)`.
6. `watcher.start()`.
7. Записати `STATE_DIR / "service.json"` (розділ 11.2), слухати.

### 9.7 WebSocket `/ws`

Підключення: `ws://127.0.0.1:8918/ws?token=<token>[&bank=<bank_id>]`.
`bank` — необовʼязковий фільтр; без нього приходить усе.

**Конверт — єдиний для всіх подій:**

```json
{"v": 1, "type": "index_progress", "ts": "2026-07-26T12:31:07.412+03:00",
 "bank_id": "9f2a1c7b3e5d0846", "data": { … }}
```

`bank_id` — `null` для подій рівня сервісу. Клієнт **зобовʼязаний ігнорувати
незнайомі `type`** (це дає нам право додавати події, не ламаючи UI).

| `type` | Коли | `data` |
|---|---|---|
| `hello` | одразу після коннекту | `{"version","banks":["<id>",…],"queue":QueueSnapshot}` |
| `queue` | будь-яка зміна глибини/поточної задачі | `{"depth","high","normal","low","current":{…}\|null}` |
| `index_start` | воркер узяв задачу | `{"task_id","kind","path","batches","trigger"}` — `batches: 0`, коли к-сть ще не відома (файл не нарізано; для `bulk` — завжди) |
| `index_progress` | після кожного закомміченого батчу | `{"task_id","path","batch","batches","chunks_done","chunks_total"}` |
| `index_done` | файл/bulk завершено | `{"task_id","kind","path","chunks_indexed","files_indexed","took_ms"}` |
| `index_error` | задача впала | `{"task_id","kind","path","error"}` |
| `index_yield` | задачу витіснено (8.3) | `{"task_id","path","resume_batch"}` |
| `prune` | шляхи знято з індексу | `{"paths":["…"],"count":2}` |
| `bank_added` | зареєстровано банк | `{"bank": BankInfo}` |
| `bank_removed` | знято банк | `{"bank_id":"…"}` |
| `bank_status` | стан банку змінився | `{"bank": BankInfo}` |
| `query` | виконано пошук (жива стрічка журналу) | `{"face","query","status","n_hits","took_ms"}` |
| `ping` | кожні 30 с | `{}` |

Клієнт → сервер: тільки `{"type":"subscribe","bank_id":"…"|null}` і
`{"type":"pong"}`. Будь-що інше ігнорується. **[NEW]**

Троттлінг: `index_progress` не частіше **1 разу на 200 мс** на задачу; останній
батч віддається завжди. **[NEW]**

### 9.8 `/mcp-tools/*` — дзеркало MCP-тулів звичайним HTTP **[NEW]**

**Для кого.** Для людини (тикнути curl-ом чи у Swagger) і для скрипта, який
перевіряє «а воно взагалі відповідає». Агенти цією поверхнею **не** ходять — їм
`/mcp?token=…` з офіційним JSON-RPC.

**Правило, з якого все випливає: це дзеркало, а не другий API.** Імена
ендпоінтів — **імена тулів дослівно**, параметри — **ті самі** й з тими самими
дефолтами, відповідь — **той самий текст**, що бачить модель. Тому дзеркало не
може розійтися з оригіналом непомітно: розбіжність видно в першому ж
порівнянні. Ніякого власного словника (`/tools/search`, свої коди, своя
пагінація) — це був би другий контракт, який розповзеться.

| Ендпоінт | Метод | Параметри (query) |
|---|---|---|
| `/mcp-tools/search` | `GET` | `query` (обовʼязковий), `top_k=5`, `path_prefix`, `bank` |
| `/mcp-tools/tree` | `GET` | `path_prefix`, `depth=3`, `bank` |
| `/mcp-tools/reindex` | `POST` | `path`, `bank`, `full=false` **[NEW]** |

`reindex` — `POST`, бо він **змінює стан** (ставить задачу в чергу);
решта читає й лишається `GET`, щоб її можна було вставити в адресний рядок.

**Дзеркало зберігає `bank`, якого в тулів звичайного обличчя вже немає — і це
не розходження. [NEW]** `search`/`tree` на `/mcp` адресуються **токеном** і
можуть відкриватися **токеном самого банку**, тож аргумент `bank` там дав би
креденшлу одного банку читати інший (10.3). Ця ж поверхня бере **сервісний**
токен, який і так дістає до кожного банку, — назвати банк у виклику не додає
жодного доступу й лишається єдиним способом руками порівняти два банки зі
свагера. `reindex` дзеркалить тепер **адмінський** тул (10.5) і несе його
`full`.

**Формат.** Типово `text/plain` — байт у байт те, що повертає тул:

```
GET /mcp-tools/search?bank=mnemo&query=черга&top_k=2

[mnemo · bank=mnemo · status=ready · queued=0 · chunks=42]
[1] topics/queue.md · Priority queue · score=0.0325
…текст секції…
```

`?format=json` — те саме в конверті, для скриптів:

```json
{"tool": "search", "bank": "mnemo", "text": "[mnemo · bank=…]\n[1] …"}
```

Поле `text` — **той самий рядок**, що й у `text/plain`. JSON тут не «структурує
відповідь по-своєму», він її лише обгортає: інакше з двох форм зробилося б два
контракти.

**Помилки — два різні класи, і їх не варто зливати:**

* **Проблема, яку бачить тул** (банку немає, назва неоднозначна) → `200` і
  **текст**, який можна прочитати: «no bank matches 'nope' / Available banks:
  …». Так поводяться тули MCP — вони не кидають, бо агентові потрібен
  наступний крок, а не трасбек, — і дзеркало повторює це дослівно.
* **Запит, що до тула не доїхав** → стандартний код і конверт 9.2: `401` без
  токена, `422` без обовʼязкового `query`. Це не відповідь тула, а відмова його
  викликати, і структурована деталь тут корисніша за прозу.

**Журнал.** Виклики пишуться в `query_events` з `face="mcp-tools"` — окремо від
`face="mcp"`, щоб ручні тики не змішувалися зі справжніми запитами агента у
статистиці.

---

## 10. Блок K — MCP

### 10.1 Транспорт **[NEW, з поясненням розбіжності]**

`Memory-implementation-v3.md` називає новою залежністю пакет **`fastmcp`**.
У `requirements.txt` уже стоїть **`mcp>=1.0.0`** — офіційний Python-SDK, у
якому клас `FastMCP` живе за адресою `mcp.server.fastmcp.FastMCP` і **вже
імпортований** у `src/mcp_server.py`. Це два різні дистрибутиви з однією
назвою класу.

**Фіксуємо:** беремо `FastMCP` з наявного `mcp` SDK і монтуємо його
streamable-HTTP-застосунок у FastAPI на шляху `/mcp` — нової залежності не
треба, а вимога «MCP по HTTP, той самий uvicorn, без спавну» (NFR-2)
виконується. Якщо на фазі 4 виявиться, що версія SDK цього не вміє,
**це розвилка для тимліда**, а не привід тягнути другий пакет мовчки.

**Чим FastAPI тут є і чим не є** (питання виникало, тому записано прямо):
MCP реалізує **FastMCP**; FastAPI його лише **хостить** через `app.mount("/mcp",
…)`. Обидва — ASGI-застосунки, mount — стандартна композиція: FastAPI віддає
все під `/mcp` всередину й у протокол не заглядає. Тобто два фреймворки не
змішані, а вкладені. Один процес і один порт — свідомо: тули кличуть внутрішні
функції бекенда **напряму** (`api_search`, `api_tree`), і окремий процес
змусив би їх ходити по HTTP — другий сокет, подвійний запис у журнал, ризик
заклинити в черзі, за якою сам і чекаєш. Окремий **порт** без окремого процесу
лишається можливим, якщо колись треба буде віддати MCP у мережу, тримаючи
`/api` на loopback; це bind-опція, не переробка.

Поверхня для **людини** — не тут, а в 9.8 (`/mcp-tools/*`): у MCP немає
OpenAPI, бо це JSON-RPC, тож свагера на `/mcp` не буває й бути не може.

### 10.2 Тули звичайного обличчя (`/mcp`)

| Тул | Аргументи | Повертає |
|---|---|---|
| `search` | `query: str`, `top_k: int = 5`, `path_prefix: str \| None = None` | текст: заголовок зі статусом + пронумеровані секції |
| `tree` | `path_prefix: str \| None = None`, `depth: int = 3` | текст: відступне дерево з заголовками |

`write` **немає** (рішення #6). **Рівно ці два, і більше нічого.**

**`bank` більше не аргумент тула. [NEW]** Було виміряно на живому сервісі:
підключення до `/mcp/mnemo` з викликом `search(bank="odin-crm")` повертало
памʼять odin-crm. Це знищує весь сенс адресації по підключенню («проєкт
тримає своє MCP на свій банк», design §6), а з появою токенів банку зробило б
токен одного банку ключем до будь-якого, який власник зуміє назвати. Банк
тепер визначає **токен, і тільки він** (10.3).

**`reindex` пішов на адмінське обличчя (10.5). [NEW]** Watcher переіндексовує
сам за секунди після збереження, тож на проєктному обличчі `reindex` — це
слот тула, витрачений у **кожній** сесії на кнопку, яку майже нікому не треба
натискати.

**Імена — без префікса, і це рішення тимліда.** Було `memory_search`,
`memory_tree`, `memory_reindex`; стало `search`, `tree`, `reindex`. Причина:
клієнт і так подає тул під іменем сервера — Claude Code показує його як
**`mcp__mnemo__search`**, — тож `memory_` вдруге повторює те, що вже є в
неймспейсі, і платить за це токенами в описі кожного тула. Побоювання «агент
сплутає з іншим `search`» знято тим самим фактом: неймспейс розводить їх до
того, як модель почне вибирати.

> **Наслідок для вже прийнятих проєктів.** `.mcp.json` імен тулів не містить,
> тому перевмикати нічого не треба. А от `.claude/rules/mnemo-memory.md`, який
> уже лежить у чужому репозиторії, згадує старі імена — і `mnemo init` його
> **не перезаписує** (він ніколи не чіпає наявний файл). Оновлення цього тексту
> — свідомий крок власника проєкту, з дифом, як і будь-яка інша правка правила.

Текст `search` починається рядком статусу, щоб агент бачив стан без
окремого поля:

```
[mnemo · bank=odin-crm · status=indexing · queued=12 · chunks=318]
[1] topics/queue.md · Priority queue · score=0.0325
…
```

Два випадки без хітів формулюються **по-різному**, бо агент має з них зробити
різні висновки (рішення #11 + правило пріоритету з 5.2):

* `status=indexing`, `chunks=0` →
  `Bank is still building its first index — retry shortly.`
* `status=empty`, `queued=0` →
  `Bank has nothing indexed yet.`
* `status=ready`, хітів немає → `No relevant results.`

### 10.3 Адресація банку **[NEW]**

**Токен визначає банк. Більше нічого.**

    http://127.0.0.1:8918/mcp?token=<bank-token>

`registry.resolve_by_token` в `auth_middleware` перетворює предʼявлений токен
на банк ще до того, як запит дійде до тула; id банку кладеться в ASGI-`scope`,
а шим `mcp_server.AuthenticatedBankASGI` піднімає його в ContextVar, який
читають тіла тулів. `Authorization: Bearer <bank-token>` працює так само — для
клієнта, що надає перевагу заголовкам.

**Що зникло і чому.** Було чотири рівні: аргумент тула, сегмент URL, заголовок
`X-Mnemo-Bank`, «якщо банк один — беремо його». Кожен рівень після першого
існував, щоб **відновити банк, якого зателефонований не назвав**. Коли токен
став адресою, відновлювати нема чого — лишаються тільки способи **не
погодитись** із креденшлом:

| Рівень | Стан | Чому |
|---|---|---|
| аргумент тула `bank` | **зник** зі схем `search`/`tree` | виміряно: `/mcp/mnemo` + `search(bank="odin-crm")` віддавав чужий банк. Лишається у спільних тілах `run_*`, бо ними ходять `/mcp-tools/*` (9.8) і адмінські тули (10.5) — обидві поверхні під **сервісним** токеном, який і так дістає скрізь |
| сегмент URL `/mcp/<name>` | **зник**; `/mcp/<будь-що>` → `400` | токен уже сказав, який банк. Сегмент був би другим голосом, вільним суперечити |
| заголовок `X-Mnemo-Bank` | **зник** — не пишеться і не читається | під токен-адресацією він міг лише суперечити креденшлу |
| «єдиний зареєстрований банк» | **зник** | здогадка, потрібна лише тоді, коли банк не названо; тепер він названий завжди |

**Дві цілі класи багів пішли разом із сегментом**, і відтворювати їх не варто:
пастка `raw_path` / `root_path` при переписуванні шляху змонтованого
застосунку (колись коштувала `404`, що читався як зламаний хендшейк) і
percent-encoding назви банку, яка на цій машині регулярно містить пробіли й
кирилицю.

**Ціна — читабельність, і вона покрита.** URL більше не каже, для якого банку
запис: кілька записів mnemo поруч відрізняються лише непрозорим токеном. Це
значення несе **імʼя MCP-запису** (`mnemo`, `mnemo-notes`) — те, що людина в
конфізі й читає. Косметичний сегмент, який маршрутизація ігнорує, **не
додається**: компонент шляху, що означає не те, що написано, гірший за
відсутній, бо наступний читач приймає його за маршрутизацію.

**Помилки, які лишились дієвими:**

* сервісний токен на `/mcp` → `401` з текстом «тут потрібен токен банку;
  сервісний належить на `/mcp-admin` або `/mcp-tools`»;
* сегмент у шляху → `400` з формою правильного URL і порадою
  `mnemo init --migrate`;
* банк **вимкнено** (`enabled: false`) → токен **автентифікується** (він
  справжній), і тул відповідає текстом «bank_not_found: bank X is disabled».
  Це навмисно: `401`, який власник не відрізнить від невірного токена, сказав
  би йому менше.

> **Поправка після фази 4, друга.** Перша редакція ставила заголовок вище за
> URL; друга зробила URL основним, лишивши заголовок сумісним запасним
> варіантом. Ця прибирає і сегмент, і заголовок: після появи токена на банк
> (6.1) обидва — це другий спосіб сказати те, що вже сказано.

### 10.4 Wiring проєкту — **дві форми, і `mnemo init` сам обирає яку** **[NEW]**

Форма залежить від одного факту: **чи є в проєкті `.mcp.json.template`.**

#### A. Проєкт із шаблоном (конвенція `project-mcp-setup`)

Такий проєкт тримає `.mcp.json` **згенерованим і в `.gitignore`**; git несе
`.mcp.json.template`, `.mcp.env.example` і `mcp-setup.sh`, який перегенеровує
`.mcp.json` з `.mcp.env`. Це не косметика: **`mcp-setup.sh` перезаписує
`.mcp.json` цілком**, тож усе, що записали прямо туди, зникає при наступному
запуску — без помилки й без сліду. Тому запис іде в шар шаблону:

```jsonc
// .mcp.json.template  (у git)
"mnemo": {
  "type": "http",
  "url": "http://127.0.0.1:{{MNEMO_PORT}}/mcp?token={{MNEMO_TOKEN}}"
}
```
```sh
# .mcp.env.example (у git) — порожнє значення + коментар
MNEMO_PORT=8918
MNEMO_TOKEN=

# .mcp.env (gitignored) — справжні значення, якщо файл уже існує
MNEMO_PORT=8918
MNEMO_TOKEN=3f1a…54ba        # токен банку — він же і є адресою
```
```sh
# mcp-setup.sh — два рядки в ЄДИНИЙ виклик sed, перед "$TEMPLATE"
  -e "s|{{MNEMO_PORT}}|${MNEMO_PORT}|g" \
  -e "s|{{MNEMO_TOKEN}}|${MNEMO_TOKEN}|g" \
```

Далі користувач запускає `bash mcp-setup.sh` — `init` про це каже прямо.
`.mcp.env` **не створюється**, якщо його немає: це був би файл із секретами,
якого проєкт не просив.

**Імена змінних походять від імені MCP-інстансу:**
`mnemo` → `MNEMO_*`, `mnemo-notes` → `MNEMO_NOTES_*`. Назви банків — людські
мітки з пробілами й кирилицею, а вони не можуть бути в імені shell-змінної;
тримаючи назву на боці **значення**, ми лишаємо shell-safe вимогу тільки до
ASCII-імені інстансу, яке обираємо ми.

#### B. Проєкт без шаблону

```json
{
  "mcpServers": {
    "mnemo": {
      "type": "http",
      "url": "http://127.0.0.1:8918/mcp?token=3f1a850618bb…"
    }
  }
}
```

Токен **літеральний**, тому `init` дописує `.mcp.json` у `.gitignore` (рівно
один рядок, якщо його там нема; порядок і решта вмісту не чіпаються).

**Якщо файл уже трекається git-ом — `init` ВІДМОВЛЯЄТЬСЯ.** Не «попереджає й
пише», а відмовляється: друкує `git rm --cached .mcp.json`, не пише нічого,
виходить з ненульовим кодом. Асиметрія і є причиною — відмову відкочують
однією командою, а токен у трекованому файлі не відкотити ніяк: поки хтось
помітить, він уже в чужому клоні. Те саме стосується `.mcp.env` у гілці з
шаблоном: це той самий літеральний токен, просто в іншому файлі.

Трекнутість визначається **читанням `.git/index`** (формати v2/v3/**v4**), а
не викликом бінаря: `init` **git не запускає ніколи** — ні щоб перевірити,
ні тим паче щоб виправити. Три відповіді, і третя теж відмовляє:

| Стан | Відповідь | Чому |
|---|---|---|
| індексу немає (свіжий `git init`, нічого не застейджено) | `False` — писати можна | інакше `init` відмовляв би саме там, де його найчастіше й запускають |
| файл є в індексі | **відмова** | сюди все й написано |
| індекс є, але не парситься | **відмова** | єдина здогадка, чия помилка невідворотна. Тому v4 таки розібраний, а не списаний в «не знаю»: `index.version=4` дехто справді вмикає, і «не знаю» зробило б `init` для них непридатним |

#### Спільне для обох форм

* **Заголовків більше немає.** `init` не пише ні `Authorization`, ні
  `X-Mnemo-Bank`. Перший дублював креденшл у друге місце заради шляху, на
  який ніхто не спирається; другий під токен-адресацією міг лише
  **суперечити** креденшлу, тому бекенд його теж більше не читає. URL —
  єдине місце, де щось із цього живе.
* **Токен — власний токен банку, не сервісний.** Він відкриває два read-тули
  одного банку. Сервісний належить кабінету, CLI й адмінському обличчю і в
  файл проєкту не потрапляє ніколи.
* **Назви банку в записі немає взагалі** — ні в шляху, ні в заголовку, ні
  у змінній. Банк визначає токен (10.3). Це прибрало заразом і
  percent-encoding назви з пробілами й кирилицею, і питання «назва чи
  `bank_id`»: жодне з двох у файл не потрапляє.
* **Який це банк — каже імʼя запису** в `mcpServers` (`mnemo`,
  `mnemo-notes`). Косметичного сегмента, який маршрутизація ігнорує, не
  додаємо.
* Кілька банків = кілька записів у `mcpServers` (`mnemo`, `mnemo-notes`, …) —
  саме так design §2 розводить ізоляцію.
* `--migrate` дістає мертві stdio-форми L1/L2 **і всередині шаблону** — саме
  там їх носить живий `voice-agent` (15.10).

> **Що змінилось проти першої редакції.** Було: `${MNEMO_API_TOKEN}` у самому
> `.mcp.json` плюс дубль у заголовках; змінну експортував інсталятор. Стало:
> у проєкті з шаблоном — `{{VAR}}`-плейсхолдери й значення в `.mcp.env`; без
> шаблону — літеральний токен банку в gitignored `.mcp.json`. Причина зміни:
> `${MNEMO_API_TOKEN}` — це **сервісний** токен, тобто найширший креденшл на
> машині, у файлі кожного проєкту.

---

### 10.5 Адмінське обличчя — `/mcp-admin` **[NEW]**

Другий FastMCP-застосунок у тому ж процесі, **без сегмента банку** в шляху.

| | `/mcp` | `/mcp-admin` |
|---|---|---|
| адресується | одному банку, сегментом URL | сервісу; сегмента немає |
| відкривається | токеном цього банку **або** сервісним | **тільки** сервісним |
| тули | `search`, `tree` | шість нижче |

| Тул | Аргументи | Повертає |
|---|---|---|
| `banks` | — | текст: усі банки зі статусом і лічильниками |
| `bank_add` | `path: str`, `name: str \| None = None` | текст: під якою назвою зареєстровано + що індексацію поставлено в чергу |
| `bank_remove` | `ref: str`, `drop_index: bool = True` | текст; `.md` не чіпаються ніколи |
| `reindex` | `bank: str`, `path: str \| None = None`, `full: bool = False` | текст: «queued N task(s)» |
| `status` | — | текст: сервіс + черга + банки |
| `logs` | `kind: str = "index"`, `bank: str \| None = None`, `n: int = 20` | текст: останні події журналу |

Тут банк — **аргумент**, і це протилежність звичайному обличчю та правильно
саме тут: підключення адресоване сервісу, а не банку, тож брати банк нема
звідки.

**Два окремі екземпляри FastMCP, а не один із фільтром по запиту.** Список
тулів клієнт кешує на хендшейку, тож «оголосили, але потім відмовили»
показало б агентові шість тулів, які він ніколи не викличе. Жоден адмінський
тул не зареєстрований на звичайному обличчі й навпаки — це перевіряється
тестом в обидва боки.

Як і на звичайному обличчі, тули кличуть внутрішні функції бекенда **напряму**
(ніякого self-HTTP) і на доменну проблему віддають **читабельний текст**, а не
викидають виняток: виняток обриває хід агента, речення каже, що робити далі.

> **Пастка, на якій це вже зламалось.** Тіла кличуть FastAPI-ендпоінти як
> звичайні Python-функції, а їхні дефолти — це `Query(...)`-дескриптори, які
> FastAPI підставляє **на запит** і яких прямий виклик не отримує. `logs`
> віддавав `TypeError: int() argument … not 'Query'`, поки його не викликали.
> Такі параметри передаються явно; у `api.py` над `api_logs` стоїть примітка
> для наступного, а в `tests/test_mcp.py` — перевірка, що **кожен** читальний
> адмінський тул реально відпрацьовує.

**Шлях без слеша.** `Mount("/mcp-admin")` у Starlette компілюється в
`^/mcp-admin/(?P<path>.*)$`, тож голий `/mcp-admin` — рівно те, що прописують
в MCP-клієнті — не матчиться й падає в `redirect_slashes`: `307` на **кожен**
запит. Тому middleware нормалізує шлях до `/mcp-admin/` перед маршрутизацією:
одна поїздка замість двох і жодного припущення, що всі клієнти йдуть за `307`
на `POST`.

---

## 11. Блок K/L — CLI та керування процесом

### 11.1 Повний перелік команд v3

| Команда | Тип | Що робить |
|---|---|---|
| `mnemo warmup` | **локальна** | явне завантаження моделі (єдиний шлях) |
| `mnemo init [--root] [--migrate] [--with-memory-hook] [--with-inject-hook]` | **локальна** | вирівнює `.mcp.json` / `.claude/` проєкту; **без прапорців хуків не пише жодного хука** **[NEW]** |
| `mnemo service start\|stop\|status\|restart` | **локальна** | FR-9, розділ 11.2 |
| `mnemo embed-server` | **локальна** | резидент моделі (руками не запускають) |
| `mnemo doctor` | **локальна** | діагностика: venv, deps, модель, токени, порти, банки; **осиротілі індекси** — лише рядок-підсумок `orphan indexes N (розмір)` і вказівка на команду; **нічого не видаляє** **[NEW]** |
| `mnemo clean-orphans [--dry-run] [--yes]` | **локальна** | єдине, що видаляє осиротілі індекси: друкує повний список (id, корінь із `meta`, розмір, `[root still on disk]`) і чекає підтвердження. Локальна навмисно — має працювати з **лежачим бекендом**, бо саме тоді дивляться на диск. Нечитаний `banks.json` → **відмова**, код 1 **[NEW]** |
| `mnemo memory-hook` | **локальна** | SessionStart-насіння: віддає `MEMORY.md` банку + карту структури; читає файл **з диска**, тому не залежить від бекенда й не може підвісити старт **[NEW]** |
| `mnemo hook-inject` | **локальна обгортка** | UserPromptSubmit-насіння: читає hook-JSON, шле `POST /api/search`; `init` його **не додає** |
| `mnemo hook-postedit` | **shim** | завжди exit 0 (реіндекс робить watcher), див. 11.3 **[NEW]** |
| `mnemo banks list` | API | `GET /api/banks` |
| `mnemo banks add <path> [--name] [--provider]` | API | `POST /api/banks` |
| `mnemo banks remove <ref> [--keep-index]` | API | `DELETE /api/banks/{id}` |
| `mnemo search <query> [--bank] [--path-prefix] [-k]` | API | `POST /api/search` |
| `mnemo reindex [--bank] [--path] [--full]` | API | `POST /api/reindex` |
| `mnemo ingest [--root]` | API, **deprecated** | аліас `reindex --bank <root>`, див. 11.3 |
| `mnemo tree [--bank] [--depth]` | API | `GET /api/tree` |
| `mnemo status` | API | `GET /api/status` |
| `mnemo logs [--kind] [--bank] [--since] [-n]` | API | `GET /api/logs` |
| `mnemo ui` | API | читає токен і відкриває браузер на `/ui/?token=…` |

Зникають: `mnemo mcp` (stdio MCP — MCP тепер HTTP), `mnemo projects`
(заміняє `banks list`).

**Сховані з `--help`, але робочі — шість команд.** `serve`, `embed-server`,
`memory-hook`, `hook-inject`, `hook-postedit`, `ingest`. Їх ніхто не набирає,
але щось їх викликає: `serve` — це те, що спавнить `service start`;
`embed-server` спавнить бекенд; три хукові — те, що запускає вже вписаний
хук Claude Code. Тому вони **сховані, а не видалені**: `hook-postedit`
взагалі існує лише для того, щоб вписаний колись хук не падав, тож його
видалення спричинило б рівно те, від чого він захищає. У `--help` лишається
**13 команд** — усі ті, які справді набирають.

Технічна деталь, варта рядка, бо коштувала одного циклу: `help=argparse.
SUPPRESS` на **субпарсері** не ховає його, а друкує `==SUPPRESS==`. Ховає
саме **відсутність** `help=` (плюс `metavar` на `add_subparsers`, щоб ім'я не
випливло у фігурних дужках usage). **[NEW]**

`src/client.py` — тонкий `httpx`-клієнт, який усі API-команди й хук ділять:

```python
class ServiceDown(RuntimeError): ...

class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 10.0) -> None
    def health(self) -> dict
    def search(self, bank, query, **kw) -> dict
    def reindex(self, bank, *, path=None, full=False) -> dict
    def tree(self, bank, *, depth=0, links=False) -> dict
    def file(self, bank, path) -> dict
    def banks(self) -> list[dict]
    def add_bank(self, root, *, name=None, provider=None) -> dict
    def remove_bank(self, bank_id, *, drop_index=True) -> None
    def status(self) -> dict
    def logs(self, kind, **kw) -> dict
```

**Мʼяка деградація (перевірка фази 4).** Бекенд лежить → `ServiceDown`; CLI
друкує один рядок пояснення й повертає **код 3** (не трасбек, не 1 — щоб
скрипт міг відрізнити «сервіс лежить» від «нічого не знайдено»). Хук у тому ж
випадку мовчки виходить `0`. **[NEW]**

### 11.2 `src/service_ctl.py` (блок L)

```python
def start(*, foreground: bool = False) -> int
def stop(*, timeout: float = 10.0) -> int
def status() -> int
def restart() -> int
def read_service_info() -> dict | None
```

**Два файли стану, два різні власники** — це не дублювання: **[NEW]**

* `STATE_DIR / "service.json"` — пише **сам бекенд** на старті й знімає на
  виході. Для звітності: версія, порт, аптайм, і щоб помітити бекенд, якого
  ми не запускали.

  ```json
  {"pid": 12345, "port": 8918, "host": "127.0.0.1",
   "started_at": "2026-07-26T09:00:00+03:00", "version": "3.0.0",
   "python": "C:/Users/dima/.claude/mnemo/.venv/Scripts/pythonw.exe"}
  ```

* `STATE_DIR / "service.pid"` — пише **тільки `service_ctl`**: чий процес ми
  спавнили і який саме це був інстанс (PID + відбиток часу створення).
  Записується атомарно через `.pid.tmp` → `replace`.

**Голий PID — не підстава щось убивати.** PID-и перевикористовуються, тож
застарілий файл із переробленим PID називає чужий процес (редактор, білд).
Термінація гейтиться `owned_process()`: PID мусить прийти з **`service.pid`**
і досі нести записаний при спавні відбиток. У `service.json` відбитка немає й
власник інший, тому він — джерело **звіту**, ніколи не ліцензія на `kill`.
Сервіс, піднятий кимось іншим, звітується як `foreign` і **не чіпається**.

`status` = живий процес з `pid` **і** успішний `GET /health`. Розбіжність
(`pid` живий, `/health` мовчить) звітується окремим станом `unhealthy`, а не
`running`. Файл-сирота (процесу немає) прибирається `start`-ом.

**Тільки той самий користувач (рішення, а не недогляд).** Сервіс працює від
**залогіненого користувача** — Task Scheduler реєструє задачу під цим
принципалом; крос-принципальна робота **не підтримується**. Процес, який ми не
маємо права відкрити, звітується `foreign`.

Спавн — windowless, без винятків (NFR-1). Windows: **`CREATE_NO_WINDOW |
CREATE_NEW_PROCESS_GROUP` + інтерпретатор `pythonw.exe`**; POSIX —
`start_new_session=True` + stdio→devnull. Це стосується **і** бекенда, **і**
embed-резидента.

> **Поправка до першої редакції (виміряно, не з памʼяті).** Тут стояло
> `CREATE_NO_WINDOW | DETACHED_PROCESS`. Так робити **не можна**: ці прапорці
> взаємно виключні — `CREATE_NO_WINDOW` документовано **ігнорується** в парі з
> `DETACHED_PROCESS` (як і з `CREATE_NEW_CONSOLE`), а сам `DETACHED_PROCESS`
> дозволяє дитині завести **справжнє видиме вікно** консолі. Тобто «захист»
> тихо ставав no-op. Головний бар'єр — **структурний**: `pythonw.exe` як
> GUI-subsystem-двійник консолі мати не може взагалі; `CREATE_NO_WINDOW` — це
> ремінь до тих підтяжок.

> **Іменований must-fix фази 5 — закрито.** `embed_server._spawn_server`
> ставив лише `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, чого **не
> досить**: така дитина, викликавши `AllocConsole()`, дістає справжнє вікно.
> Тепер резидент не збирає прапорці сам, а делегує в
> `service_ctl.spawn_detached` / `windowless_python()` — **один примітив на
> всі спавни**, тож «жодного вікна не блимає» тримається однаково для бекенда
> й для резидента, який автостартує з клієнта.

#### 11.2.1 Пастка «лаунчер ≠ процес, що працює» (Windows) **[NEW]**

Найчастіше перевідкрита вада всієї збірки: **тричі поспіль код міркував про
процес, який він *запустив*, а не про той, що *робить роботу*.**

**Механізм.** На Windows `.venv\Scripts\python.exe` (і `pythonw.exe`) — це
**redirector-заглушка**: вона стартує справжній інтерпретатор як свою дитину й
часто одразу виходить. Наслідки, які ламають наївні припущення:

* PID, що повернув `Popen`, — це ~4 МБ заглушки, а не сервісу;
* сокет тримає й модель у памʼяті тримає **дитина**;
* ця дитина рапортує шлях **системного** Python, а не венвівського.

**Три симптоми, які це вже дало:**

1. **`service.pid` записував заглушку.** `stop` випадково працював — redirector
   тримає дитину в job-обʼєкті, а ми вбиваємо дерево, — але `status` міг
   звітувати «зупинено», поки бекенд обслуговує, а заглушка, що вийшла сама,
   змусила б `stop` відмовитись.
2. **`doctor` і `service status` показували два різні PID на один сервіс** —
   `service.json` несе PID того, хто обслуговує, `service.pid` — того, хто
   запускав. Обидва праві, і збивають з пантелику рівно там, куди дивиться вже
   спантеличений користувач.
3. **Запобіжник тест-раннера виключав процеси за шляхом до виконуваного
   файлу**, щоб не вбити сервіс користувача, — і не впізнав його, бо дитина
   рапортує системний python. Бекенд користувача було вбито двічі, вдруге —
   саме тим запобіжником, який для цього й існував.

**Правило (це і є те, що варто памʼятати).** Запобіжник вартий рівно того
атрибута, за яким він ключується, а всі три провали ключувались на тому, чого
реальний процес **не несе**:

* **Новизна — не власність.** «Зʼявився після мого знімка» дорівнює «мій» лише
  на тихій машині (саме так teardown-реап повбивав чужих резидентів).
* **Шлях до бінарника — не ідентичність.**
* **Працює** те, що є **фактом про машину й перевіряється ззовні**: *хто
  тримає цей порт*, або **відбиток `(pid, час створення)`**, знятий у момент
  спавну. Не здогадка про те, який процес *мав би* бути нашим.

### 11.3 Сумісність зі вже підключеними проєктами **[NEW]**

У проєктах, що вже прийняли v2, у git лежать хуки `mnemo ingest` (SessionStart)
і `mnemo hook-postedit` (PostToolUse) та stdio-запис `.mcp.json`. Після
оновлення рушія вони почнуть викликати v3-бінарник. Щоб нічого не ламалося до
того, як власник проєкту сам перезапустить `mnemo init`:

* `hook-postedit` лишається командою, але **нічого не робить** і повертає `0`
  (реіндекс — робота watcher, рішення #15).
* `ingest` лишається як deprecated-аліас `reindex`, з попередженням у stderr.

**`mnemo init` зберігає семантику v2 — рішення тимліда.** Це команда, яку
запускає **людина** (або skill `mnemo-adopt`); ні сервіс, ні watcher, ні
інсталятор її **ніколи не викликають**. Її властивості лишаються ті самі, що
описані в докстрінгу `scaffold.py`:

* **явна** — тільки на запит;
* **адитивна** — додає лише власні ключі, чужі не чіпає й не переупорядковує;
* **ідемпотентна** — повторний запуск нічого не змінює;
* **відмовляється при конфлікті** — пише звіт і **не змінює нічого**.

Режим **`mnemo init --migrate`** — вужчий, ніж здається: він має право
переписати **лише те, що mnemo сама колись і написала**:

| Об'єкт | `--migrate` |
|---|---|
| `mcpServers.mnemo` у легасі-формі **L1** (`/bin/sh -c … mnemo mcp`) | переписує на HTTP-форму 10.4 |
| `mcpServers.mnemo` у легасі-формі **L2** (`type: stdio`, `${HOME}/…/bin/mnemo`, `args:["mcp"]`) | переписує на HTTP-форму 10.4 |
| `hooks.SessionStart` з командою `… mnemo ingest` | прибирає |
| `hooks.PostToolUse` з командою `… mnemo hook-postedit` | прибирає |
| `hooks.UserPromptSubmit` з командою `… mnemo hook-inject` | лишає (актуальний) |
| `MNEMO_BANK` у `.mcp.env` / `.mcp.env.example` і його `sed -e` рядок у `mcp-setup.sh` | **прибирає [NEW]** — змінна, якої шаблон більше не містить |
| `mcpServers.mnemo` у **невпізнаній** формі | **відмова + звіт** |
| будь-який хук чи ключ, якого mnemo не писала | **відмова + звіт**, ніколи не переписує |

Усе це діє **і в `.mcp.json.template`**, якщо він у проєкті є (10.4A) — саме
там ці легасі-форми й носить живий `voice-agent`. Ключ `mnemo` там такий
самий власний запис mnemo, як і в `.mcp.json`; решта шаблону — чужа й
недоторкана. **[NEW]**

**Одне уточнення до «не змінює нічого при відмові». [NEW]** Гарантія
стосується **файлів проєкту**, і вона повна: `init` спершу проганяє
валідаційний прохід (з токеном-заглушкою — жодна відмова від його значення не
залежить), і лише якщо ніщо не відмовилось, створює насіння, реєструє банк і
рендерить той самий план по-справжньому. Тобто при відмові не з'являється ні
`.claude/`, ні запис у реєстрі.

**Легасі-форм саме дві**, і друга новіша за першу: L2 прийшла з гілки
`feat/windows-native-support` і є **поточним** значенням
`scaffold._MCP_SERVER`. Повний розбір поколінь — у 15.2. Розпізнавати треба
обидві: L1 лишилась у проєктах, прийнятих до windows-гілки, L2 — у прийнятих
після неї.

**Видалення застарілих змінних — тільки `--migrate`, і тільки свого рядка.**
Два обмеження, і друге важливіше за перше:

* **звичайний `init` не видаляє нічого.** Адитивність — та властивість, заради
  якої команді довіряють, і «воно прибрало лише власний ключ» не той виняток,
  на який її варто витрачати.
* **`mcp-setup.sh` — файл користувача, не mnemo.** Його написав скіл
  `project-mcp-setup`; mnemo лише **дописувала** в нього рядки. Тому
  видаляється рядок, що дослівно збігається з тим, який mnemo сама б і
  написала (з точністю до відступу), а не «будь-який рядок із нашим
  плейсхолдером». Рядок, відредагований руками, лишається на місці, і про
  нього пишеться в звіті: це чийсь намір, а вгадувати його — саме той спосіб
  зіпсувати проєкту здатність перегенерувати власний `.mcp.json`. Нічого
  іншого в скрипті не переформатовується, не переставляється й не
  переписується.

Показ діфа й питання користувачеві — робота **skill-а `mnemo-adopt`**, не CLI.
CLI лишається детермінованим примітивом: або зробив рівно описане, або
відмовився й пояснив.

---

## 12. Конфіг: усі змінні середовища

| Env | Типово | Секція / власник | Призначення |
|---|---|---|---|
| `MNEMO_HOME` | `~/.claude/mnemo` | paths / engine-dev | корінь встановленого рушія |
| `MNEMO_STATE_DIR` | `$MNEMO_HOME/state` | paths / engine-dev | записуваний стан: індекси, токени, `service.db` |
| `MNEMO_ROOT` | — | paths / engine-dev | корінь проєкту для `init` (лишається з v2) |
| `MNEMO_PROVIDER` | `local` | providers / engine-dev | провайдер сервісу за замовчуванням |
| `MNEMO_EMBED_BIND` | `127.0.0.1` | daemon / engine-dev | адреса, яку слухає резидент |
| `MNEMO_EMBED_HOST` | `127.0.0.1` | daemon / engine-dev | адреса, яку набирає клієнт |
| `MNEMO_EMBED_PORT` | `8917` | daemon / engine-dev | порт резидента |
| `MNEMO_EMBED_IDLE_TIMEOUT` | `0` **(змінено)** | daemon / engine-dev | `0` = **немає idle-виходу**; резидент і далі підіймається **на першу потребу** й гине разом із `mnemo service stop` (не «висить завжди»). Старе `1800` коштувало ~9 с на першому пошуку після півгодинної паузи **[NEW]** |
| `MNEMO_EMBED_THREADS` | `cpu*3//4` | daemon / engine-dev | стеля ONNX-потоків (NFR-5, фаза 0) |
| `MNEMO_EMBED_POOL` | `1` | daemon / engine-dev | к-сть інстансів резидента — **не реалізовано**: конкурентність дали дві смуги в одному процесі (implementation §4) |
| `MNEMO_BATCH_SIZE` | `16` | indexer / engine-dev | чанків на батч і на коміт |
| `MNEMO_FILE_MAX_BYTES` | `2097152` | indexer / engine-dev | ліміт `GET /api/file` **[NEW]** |
| `MNEMO_BANKS_FILE` | `$STATE_DIR/banks.json` | registry / service-dev | шлях реєстру **[NEW]** |
| `MNEMO_API_HOST` | `127.0.0.1` | api / service-dev | bind бекенда **[NEW]** |
| `MNEMO_API_PORT` | `8918` | api / service-dev | порт бекенда **[NEW]** |
| `MNEMO_API_TOKEN` | з `$STATE_DIR/api.token` | api / service-dev | токен; env перекриває файл **[NEW]** |
| `MNEMO_API_URL` | `http://127.0.0.1:8918` | api / service-dev | база для `client.py` **[NEW]** |
| `MNEMO_QUEUE_PRIORITY` | `1` | queue / service-dev | `0` → чиста FIFO без витіснення |
| `MNEMO_WORKERS` | `1` | queue / service-dev | воркерів індексації **[NEW]** |
| `MNEMO_DEBOUNCE_MS` | `800` | watcher / service-dev | схлопування шторму збережень **[NEW]** |
| `MNEMO_RECONCILE_ON_START` | `1` | watcher / service-dev | наздогнати зміни, зроблені поки сервіс лежав **[NEW]** |
| `MNEMO_LOG_RETENTION_DAYS` | `30` | logs / service-dev | NFR-8 |
| `MNEMO_LOG_MAX_ROWS` | `200000` | logs / service-dev | backstop за рядками, `0` = вимкнено **[NEW]** |
| `MNEMO_API_EMBED_URL` | — | providers / engine-dev | ендпоінт `api`-провайдера (фаза 7) **[NEW]** |
| `MNEMO_API_EMBED_KEY` | — | providers / engine-dev | ключ `api`-провайдера **[NEW]** |
| `MNEMO_API_EMBED_MODEL` | — | providers / engine-dev | модель `api`-провайдера **[NEW]** |
| `MNEMO_API_EMBED_DIM` | — | providers / engine-dev | розмірність `api`-провайдера (обовʼязкова) **[NEW]** |

Зникають з v2: `INJECT_LOG_*` (JSONL більше немає).

---

## 13. Порядок реалізації по власниках

Хто на що чекає — щоб паралельна робота не впиралась у тупик.

| Фаза | engine-dev | service-dev | platform-dev | ui-dev |
|---|---|---|---|---|
| 0 | B, C, chunker-offsets, потоки A | — | — | — |
| 1 | D | — | — | — |
| 2 | H (`path_prefix`, чистий `search`) | G, I, J (без WS) | нові deps у інсталятори | — |
| 3 | — | E, F, WS | — | — |
| 4 | — | K: `client`, `cli`, `mcp_server`, `scaffold` | — | — |
| 5 | — | — | L, автозапуск, `install.ps1` | — |
| 6 | — | ендпоінти `/api/file`, `/api/tree` доводяться | — | `webui/` |
| 7 | `providers/api.py` (пул A знято з плану — див. implementation §4) | — | — | — |

Розблокування, які треба зробити рано:

* **Розділ 3 (схема) — блокує все.** engine-dev має закрити C у фазі 0, бо на
  ній стоять і D, і H, і J.
* **Розділ 6 (реєстр) блокує J**, а J блокує K і UI. service-dev починає з G.
* **ui-dev не чекає на фазу 6:** розділи 9.5 і 9.7 — повний контракт, по ньому
  можна писати кабінет на моках і зустрітися з бекендом уже готовим.

---

## 14. Рішення тимліда за цим документом

Питання, які документ виносив на розвилку, **закриті**. Записано тут, щоб їх
не переоткривали.

| # | Питання | Рішення | Де в тексті |
|---|---|---|---|
| 1 | Адреса банку в git-трекованому `.mcp.json` | ~~назва, не `bank_id`~~ → **переглянуто (див. #20)**: у файл не потрапляє **жодна** форма назви — банк визначає токен. Унікальність назв реєстр тримає й далі, але вже не заради wiring | 6.5, 10.3, 10.4 |
| 2 | Чи чіпає `mnemo init` git-трековані файли чужих проєктів | зберігає семантику v2 (явна, адитивна, ідемпотентна, відмова при конфлікті); `--migrate` переписує **лише власні** легасі-ключі mnemo; діф і питання — робота skill-а | 11.3 |
| 3 | Гранульованість відновлення після падіння | **файл**; per-batch resume свідомо відкладено (потрібен персистентний прогрес; детермінований `chunk_uid` уже виключає дублікати) | 4.2 |
| 4 | Секційна власність `config.py` | прийнято як описано | 1.1 |
| 5 | Як кабінет дає вибрати корінь нового банку | **браузер тек на боці бекенда** (`GET /api/fs/dirs`) плюс поле для вставленого шляху; нативний системний діалог **відкинуто** — бекенд це відчеплений windowless-процес, його модалка вилазить за вікном браузера або не з'являється взагалі, а під Linux вимагає `zenity`/`kdialog`, яких може не бути | 9.5, FR-7 |
| 6 | Чи є «публічний REST» і як його тикати | є **дзеркало** `/mcp-tools/<tool_name>` — імена, параметри й текст відповіді 1:1 з MCP-тулами, `?format=json` лише обгортає той самий рядок. Другого API зі своїм словником **немає**. `/api/*` стає **приватним**: `include_in_schema=False`, у свагері не видно | 9.1, 9.8, FR-5 |
| 7 | Чому Swagger не працював і що з цим | у схемі не було `securitySchemes`, тому кнопки **Authorize** не існувало й будь-який «Try it out» давав `401`. Додано HTTP-bearer; `/mcp-tools` приймає ще й `?token=`, бо це поверхня для `curl` | 9.1 |
| 8 | MCP окремим процесом/портом? | **ні** — FastMCP лишається змонтованим у бекенд (один процес, один порт), бо тули кличуть внутрішні функції напряму; окремий процес змусив би їх ходити по HTTP (другий сокет, подвійний журнал, ризик заклинити в черзі). Окремий **порт** без окремого процесу лишається як можлива bind-опція | 10.1 |
| 9 | Хуки | `init` не додає **жодного**; два насіння за прапорцями: `memory-hook` (SessionStart, `MEMORY.md` + карта, читає з диска) і `hook-inject` (UserPromptSubmit, top-N). Дисципліну тримає **правило**. Скасовано попереднє «даємо готові приклад-хуки»: авто-інжект створює фальшиве відчуття, що агент уже шукав | 11.1, design #15, FR-5a |
| 10 | Як не затягнути в банк скіли/агентів/рули | **вкладеною розкладкою, не ексклюдами**: уся памʼять під `.claude/memory/` (`logs/`, `topics/`, `agents/<role>/`), корінь банку — саме `memory`. Механізму ексклюдів (`exclude` у реєстрі, `.mnemoignore`) **не заводимо**: межа теки — позитивне правило, список винятків гниє (нову теку забув дописати — вона тихо в індексі) | design #18, FR-1a |
| 11 | Правило памʼяті | **портативне ядро + тонка CC-обгортка**: ядро (структура, тригери, дисципліна запису, імена тулів) мусить працювати вставленим у system prompt будь-якого агента, бо MCP уже стандартний | design #19, 15.9 |
| 12 | Імена тулів | **без префікса**: `search`, `tree`, `reindex` (було `memory_*`). Клієнт і так подає тул як `mcp__mnemo__search` — префікс дублює неймспейс і платить токенами в описі кожного тула. Шляхи дзеркала йдуть за іменами: `/mcp-tools/search` | 10.2, 9.8 |
| 13 **[NEW]** | `bank` як аргумент тула | **прибрано зі звичайного обличчя**. Виміряно: `/mcp/mnemo` + `search(bank="odin-crm")` віддавав чужий банк — адресація по підключенню не працювала. Спільні тіла `run_*` аргумент **зберігають**: ними ходять `/mcp-tools/*` і адмінські тули, обидва під сервісним токеном | 10.2, 10.3, 9.8 |
| 14 **[NEW]** | Де живе `reindex` | **на адмінському обличчі**. Watcher переіндексовує сам за секунди, тож на проєктному обличчі це слот тула, витрачений у кожній сесії на кнопку, яку майже нікому не треба | 10.2, 10.5 |
| 15 **[NEW]** | Друге обличчя MCP | `/mcp-admin` — окремий FastMCP-екземпляр, без сегмента банку, шість тулів керування, **тільки сервісний токен**. Два екземпляри, а не один із фільтром: список тулів кешується на хендшейку | 10.5 |
| 16 **[NEW]** | Токен на банк | реєстр карбує 48-hex токен при реєстрації; наявні банки отримують його **міграцією** (додавання поля, не перезапис). Матриця доступу — у 9.1. Це **найменша достатня привілея, а не стіна**: сервісний токен і далі відкриває все, і це прийнято | 9.1, 6.1 |
| 17 **[NEW]** | Що пише `mnemo init` у проєкт із `.mcp.json.template` | **у шаблон**, плейсхолдерами, плюс значення в `.mcp.env` і `sed`-рядки в `mcp-setup.sh`. Прямий запис у `.mcp.json` там стирається наступним `bash mcp-setup.sh` — тихо. Без шаблону — прямо в `.mcp.json` з літеральним токеном + рядок у `.gitignore` | 10.4 |
| 18 **[NEW]** | Як `init` дізнається, що `.mcp.json` трекається, і що робить | **читає `.git/index`** (v2/v3/v4), git не запускає взагалі. І — **відмовляється писати**, а не попереджає: відмову відкочують однією командою, токен у трекованому файлі — ніяк. «Не знаю» (індекс не парситься) теж відмовляє; «індексу немає» — ні, бо це свіжий репозиторій | 10.4 |
| 19 **[NEW]** | Заголовки в `.mcp.json` | **`init` їх більше не пише, а бекенд `X-Mnemo-Bank` більше не читає.** `Authorization` дублював креденшл у друге місце заради шляху, на який ніхто не спирається; `X-Mnemo-Bank` під токен-адресацією міг лише суперечити креденшлу | 10.4, 10.3 |
| 20 **[NEW]** | Чим адресується банк на `/mcp` | **токеном, і нічим іншим.** Сегмент шляху прибрано; `/mcp/<будь-що>` → `400` з формою правильного URL. Сервісний токен `/mcp` **не відкриває** — йому нема до якого банку резолвитись, і прийняти його означало б вгадувати. Разом із сегментом пішли `X-Mnemo-Bank`, правило «єдиний банк», `MNEMO_BANK` і percent-encoding назви. Читабельність несе **імʼя MCP-запису**, не косметичний сегмент | 9.1, 10.3, 10.4 |
| 21 **[NEW]** | Чи прибирає `init` власні застарілі змінні (`MNEMO_BANK`) | **так, але тільки під `--migrate`** — звичайний `init` не видаляє нічого. У `mcp-setup.sh` (файл користувача!) знімається лише рядок, дослівно рівний тому, який mnemo сама писала; відредагований руками — лишається й згадується у звіті | 11.3 |
| 22 **[NEW]** | Чи читає бекенд `X-Mnemo-Bank` | **ні, видалено.** Під токен-адресацією це вхід, який може лише суперечити креденшлу, а режим відмови найгірший з можливих: запит, що успішно відпрацював по **чужому** банку й має цілком нормальний вигляд. Разом із ним прибрано й правило «єдиний зареєстрований банк» на звичайному обличчі | 10.3 |

**Один розворот проти першої редакції:** пріоритет статусу змінено з
`empty > indexing` на **`indexing > empty`** (5.2). Причина — придатність до
повтору: `empty` спонукає агента списати банк, `indexing` каже повернутись.
Щоб нічого не загубити, у відповідь додано **`chunk_count`** поруч із
`queued`, і тепер обидві пари читаються однозначно (таблиця в 5.2).

Ухвалено без змін: MCP через наявний `mcp` SDK замість окремого `fastmcp`;
видалення `inject_log.py` у фазі 2; `chunk_uid`; `GET /api/file` та
`start_char`/`end_char`; порт 8918; токен у `state/api.token` (Bearer для HTTP,
`?token=` для WS); 12-кодовий конверт помилки й 13 типів WS-подій; витіснення
в межах одного батчу; `bank_id` у нижньому регістрі на Windows;
`MNEMO_EMBED_IDLE_TIMEOUT` → `0`; чистий `search.py` зі статусом, зібраним у
`api.py`; v2-шими + `mnemo init --migrate`.

`DETACHED_PROCESS` в `embed_server.py:164` піднято до **іменованого must-fix
фази 5** (11.2), а не «прибирання за нагоди».

---

## 15. Спадок merge windows-native: що суперечить v3

Гілку `feat/windows-native-support` влито у `feat/v3` комітом `45e2225`
(її зміст — `5ef2b54`). Вона принесла **протестований, робочий** код, який
описує **світ v2**: MCP по stdio, скоупи `project`/`agent`, інсталятор без
сервісу. Частина цього прямо суперечить контрактам вище. Нижче — повний
перелік зіткнень; **жодне з них тут не виправляється**, лише фіксується
власник і фаза.

> Стан на момент запису перевірено емпірично, а не з памʼяті:
> `tests/test_platform.py` **проходить 17/17** на поточному дереві, а
> `tests/test_mcp.py` **уже зламаний** (див. 15.3). Обидва факти важливі:
> перший означає, що частина тестів пройде й після того, як їхній сенс
> зникне; другий — що борг уже наступив, а не чекає фази 4.

### 15.1 Зведена таблиця зіткнень **[NEW]**

| # | Зіткнення | Власник | Фаза | Розвʼязаний стан |
|---|---|---|---|---|
| 1 | `scaffold._MCP_SERVER` — stdio `${HOME}/…/bin/mnemo` + `args:["mcp"]` проти HTTP-форми §10.4 | service-dev | 4 | `_MCP_SERVER` = HTTP-словник §10.4 |
| 2 | Тепер **дві** легасі-форми MCP, а таблиця §11.3 знала одну | service-dev | 4 | `--migrate` розпізнає обидві (15.2) |
| 3 | `test_platform.test_scaffold` — «portable MCP definition» стає вакуумним, «legacy … conflict» покриває одну форму з двох | tester | 4 | переписані асерти (15.3) |
| 4 | `test_mcp.py` — спавнить `mnemo mcp` по stdio й **встановлений** лаунчер (не це дерево) | tester | 4 | HTTP-клієнт MCP; ізоляція перевіряється між **банками** |
| 5 | `mnemo mcp` як підкоманда зникає | service-dev | 4 | CLI §11.1 без `mcp` |
| 6 | `mnemo_bootstrap.py` — контракт **тримається**, але потрібен windowless-двійник | platform-dev | 5 | `[project.gui-scripts]` → `mnemow.exe` (15.4) |
| 7 | CI не знає нової інфри (порт, watchdog, фонові процеси) | tester + platform-dev | 2–5 | правила з 15.5 |
| 8 | `install.ps1` / `install.sh` не знають про сервіс, токен, автозапуск | platform-dev | 5 | чекліст 15.6 |
| 9 | `install.ps1` лочить `mnemo.exe`; тепер лочитиме й запущений бекенд | platform-dev | 5 | stop → refresh → start (15.6) |
| 10 | `EMBED_THREADS` `cpu/3 → cpu*3/4` проти виміряних чисел арени в `embedder.py` | engine-dev + tester | 0 | переміряти RSS (15.7) |
| 11 | Строгий `is_model_cached()` змінює поведінку фолбеку провайдера | engine-dev | 0 | текст помилки розрізняє «немає» й «неповний» (15.7) |
| 12 | `CLAUDE_PROJECT_DIR` — два механізми резолву кореня поряд | service-dev | 4 | хук шле `CLAUDE_PROJECT_DIR`, не `cwd` (15.8) |
| 13 | `_MEMORY_RULE` у `scaffold.py` вчить `scope`/`agent-memory` | service-dev | 4 | переписаний під плаский банк (15.9) |
| 14 | `Setup-design.md` фіксує stdio-контракт `.mcp.json` у прозі | docs-keeper | 4 | переписано під §10.4 |
| 15 | `README.md`, `CLAUDE.md`, скіл `mnemo-adopt` описують v2 | docs-keeper | 4–6 | синхронізовано |
| 16 | Живий `voice-agent`: v2-хуки в git, `.mcp.json.template`, 147 файлів у 6 скоупах | тимлід + користувач | до 4 | рішення про розкладку банків (15.10) |

### 15.2 MCP-wiring: легасі-форм тепер дві, а не одна **[NEW]**

Це **виправлення до §11.3 цього ж документа**. Коли писалася таблиця
`--migrate`, я знав одну легасі-форму. Merge додав другу, і вона новіша:

| Покоління | Форма `mcpServers.mnemo` | Звідки |
|---|---|---|
| L1 (найстарша) | `{"command": "/bin/sh", "args": ["-c", "exec \"$HOME/…/bin/mnemo\" mcp"]}` | v2 до windows-гілки |
| L2 (windows-гілка) | `{"type": "stdio", "command": "${HOME}/.claude/mnemo/bin/mnemo", "args": ["mcp"]}` | `5ef2b54`, **поточний** `_MCP_SERVER` |
| v3 (ціль) | HTTP-словник §10.4 | контракти |

`--migrate` мусить впізнавати **L1 і L2** й переписувати обидві; звичайний
`init` мусить на обох **відмовлятися зі звітом**. Таблиця в §11.3 читається з
цією поправкою: рядок «`mcpServers.mnemo` у формі v2» означає **будь-яку з L1,
L2**, а не лише `/bin/sh`.

**Що при цьому НЕ вмирає.** Портативна машинерія windows-гілки потрібна далі,
просто вужче:

* `_LAUNCHER = "~/.claude/mnemo/bin/mnemo"` лишається — на ньому тримається
  хук `hook-inject` (§11.1), а він у v3 зберігається.
* Правило «`bin/mnemo` без розширення, ОС сама дорезолвлює `.exe`» лишається.
* Гарантія `HOME == PowerShell ~` в `install.ps1` лишається **потрібною для
  хуків**, але вже **не потрібною для MCP** (в HTTP-формі `${HOME}` немає
  взагалі). Текст помилки інсталятора («requires both to match for portable
  MCP and hook wiring») стане наполовину неправдивим — це рядок для
  platform-dev у фазі 5, і його ж пінить `test_install_windows.py`
  (`assert "requires both to match" in stderr`), тож міняти текст і тест треба
  разом.

### 15.3 Тести: що вмирає, що змінює сенс **[NEW]**

**`tests/test_mcp.py` — переведений на `path_prefix`, помре у фазі 4.**
Фаза 0 прибрала `scope`/`agent` з `search` і **тим самим комітом**
(`7560221`) перевела тест: рядок 88 передає
`"path_prefix": ".claude/agent-memory/reviewer"`, асерт ізоляції став
перевіркою шляху. Жодного `scope=`/`agent=` у файлі не лишилось.

Що з ним усе-таки не так — дві речі, обидві не «червоне зараз»:

* він спавнить **встановлений** лаунчер
  (`~/.claude/mnemo/bin/mnemo[.exe]`), тобто ганяє **старе дзеркало**
  рушія, а не це дерево. Це *неперевірене*, а не зламане, і минає, щойно
  platform-dev переставить рушій;
* у фазі 4 помирає весь транспорт: `StdioServerParameters` + підкоманда
  `mcp`, якої не буде.

*Розвʼязаний стан:* тест переписується на HTTP-клієнт MCP проти піднятого
in-process застосунку; асерт ізоляції піднімається з `path_prefix` до
**ізоляції між двома банками** — саме вона у v3 є межею доступу
(рішення #13), а `path_prefix` лишається зручністю навігації й перевіряється
окремо.

**`tests/test_platform.py` — проходить, і в цьому пастка.** Три місця:

| Асерт | Зараз | Після фази 4 | Що має асертити |
|---|---|---|---|
| `portable MCP definition` | зелений | **зелений, але порожній** — порівнює `_MCP_SERVER` сам із собою | **[ЗРОБЛЕНО]** `_check_mcp_shape` перевіряє явну HTTP-форму окремо для двох файлів: у шаблоні — названі поіменно `{{MNEMO_PORT}}` і `{{MNEMO_TOKEN}}` і **жодного літерального секрету**; у `.mcp.json` — літеральний 48-hex токен банку |
| `legacy MCP definition is an explicit conflict` | зелений на L1 | **дірка** — L2 не покрита | **[ЗРОБЛЕНО]** відмова на **обох** L1 і L2, плюс перевірка, що `--migrate` переписує кожну |
| `MCP idempotent` / `hooks idempotent` | зелений | лишається валідним | без змін |

**Асерт про машинозалежні значення переписано, бо змінилось саме правило.**
«`X-Mnemo-Bank` — не 16-hex» був машинним regression-guard'ом для «у
git-трекований файл не потрапляє нічого машинозалежного». Заголовка більше
немає, а `.mcp.json` тепер **навмисно** несе літеральний токен (він
git-ігнорований), тож правило розділилось надвоє, і обидві половини
перевіряються буквально:

* **у git-трекований шар** (`.mcp.json.template`, `.mcp.env.example`) не
  потрапляє жоден літерал — ні секрет, ні назва банку, ні шлях;
* **у жодну з форм** не потрапляє сегмент шляху: `«MCP url has no path segment
  after /mcp»` — це той асерт, що впаде, якщо сегмент колись повернеться,
  зокрема як «косметична» мітка.

**`test_index_paths` — змінив сенс мовчки.** Він писався під обхід за
скоупами (`.claude/memory` + `.claude/agent-memory/<agent>`), а тепер
`_disk()` — плаский обхід кореня. Тест зелений **випадково**: фікстура кладе
`.md` рівно туди, куди дивився старий обхід. Він більше не доводить того, що
в назві. *Розвʼязаний стан:* фікстура з файлом **поза** `.claude/`, щоб тест
доводив плаский обхід, і окремий асерт, що `exclude`-глоби (§6.1) працюють.

**`test_model_cache_validation`** — не зачеплений v3, лишається як є.

### 15.4 `mnemo_bootstrap.py` — контракт тримається, з однією добудовою **[NEW]**

Пряма відповідь на питання: **так, тримається без змін.** Bootstrap робить
три речі — знаходить engine home від `sys.prefix`, ставить `MNEMO_HOME`,
імпортує `src.cli:main`. v3 зберігає і `src/cli.py`, і `main()`, тож жоден із
трьох кроків не зачеплений тим, що всередині CLI тепер HTTP-клієнт.

**UTF-8-налаштування лишається потрібним** — і це не самоочевидно, бо його
писали «for hook and MCP pipes», а MCP більше не pipe. Воно тримається на
трьох інших споживачах: `hook-inject` друкує українські секції в stdout для
інжекту; `mnemo search` / `logs` друкують український вміст у Windows-консоль
з не-UTF8 кодовою сторінкою; `mnemo status` виводить назви банків. Прибрати
його — регресія. *Дія:* лише оновити коментар, щоб він не називав MCP.

**Добудова для фази 5.** `pyproject.toml` оголошує тільки
`[project.scripts] mnemo` → консольний `mnemo.exe`. Для тихого спавну
бекенда й резидента (NFR-1) зручний **другий** entry point:
`[project.gui-scripts] mnemow = "mnemo_bootstrap:main"` → `mnemow.exe`,
зібраний під `pythonw`. Це рівно те, чого правила вимагають замість самого
`DETACHED_PROCESS`. Альтернатива — `CREATE_NO_WINDOW` над наявним
`mnemo.exe`; вибір за platform-dev, але **варіант має бути свідомим**, бо
зараз його немає взагалі.

### 15.5 CI: що можна додати і що зламається **[NEW]**

Поточна матриця (`.github/workflows/ci.yml`) ставить `requirements.txt` і
ганяє `tests/test_platform.py` на 4 конфігураціях + `test_install_windows.py`
на Windows. Її головна цінність — **модельна незалежність**: жоден тест не
тягне 2.2 ГБ. Це властивість, яку не можна втратити.

**Можуть приєднатися** (усі модельно-незалежні, з фейковим провайдером, що
віддає детерміновані вектори потрібної розмірності):

* store (C): схема, `needs_rebuild` на зміну `provider_key`, детермінізм
  `chunk_uid`, prune;
* chunker: `start_char`/`end_char` збігаються з нарізкою тексту;
* search (H): нормалізація й межа сегмента в `path_prefix` (§5.1) — чиста
  логіка, БД можна наповнити фейковими векторами;
* registry (G): `bank_id`, унікальність назв, три форми `resolve`;
* servicelog (I): схема, фільтри, retention;
* queue (E): пріоритети, дедуп, витіснення — з фейковим індексатором;
* API (J): усі ендпоінти через `httpx.ASGITransport` **in-process**.

**Що зламається, якщо робити наївно:**

| Пастка | Чому | Правило |
|---|---|---|
| `uvicorn` слухає реальний порт | 8918 може бути зайнятий, а на runner-ах бувають обмеження | тестувати через `ASGITransport`/`TestClient` **без сокета**; якщо сокет справді потрібен — `port=0` і читати призначений |
| watchdog у CI | FSEvents на macOS має помітну затримку, `ReadDirectoryChangesW` — свою; `sleep(0.5)` + assert буде мигати | **опитувати з дедлайном** (до 10 с), ніколи не фіксований sleep; окремий job, не в загальній матриці |
| фонові процеси (`service start`) | лишають сироту й вішають job | teardown у `finally`; окремий job; на macOS — пропускати |
| `uvicorn[standard]` | тягне `httptools`; `uvloop` **не** ставиться на Windows (marker `sys_platform != "win32"`) — це очікувано, не помилка | перевірити, що встановлення проходить на всіх 4 конфігураціях, перш ніж додавати тести |
| нові тести й модель | будь-який імпорт, що торкається `TextEmbedding`, ризикує потягнути завантаження | фейковий провайдер за інтерфейсом §2, `MODEL_CACHE` — під `patch`, як уже робить `test_model_cache_validation` |

### 15.6 Чекліст інсталяторів для фази 5 (platform-dev) **[NEW]**

Не реалізація — перелік того, чого в `install.ps1` / `install.sh` сьогодні
немає, а фаза 5 вимагає.

1. **Експорт `MNEMO_API_TOKEN`.** Windows — користувацька змінна (тим самим
   обережним способом, яким уже ставиться `HOME`: тільки якщо відсутня,
   ніколи не перезаписувати); Linux — shell-профіль. Без цього `${…}` у
   git-трекованому `.mcp.json` (§10.4) не розкриється й MCP не підключиться.
2. **Windowless entry point** — `mnemow.exe` через `[project.gui-scripts]`
   або усвідомлений `CREATE_NO_WINDOW` (15.4).
3. **Автозапуск.** Windows — прихована задача Task Scheduler на вхід
   користувача; Linux — `systemd --user` unit + `loginctl enable-linger`
   (NFR-6).
4. **Зупинка сервісу перед refresh — новий обовʼязковий крок.** Сьогодні
   `Install-Launcher` уже вміє впертись у залоченый `mnemo.exe` і каже
   «закрийте сесії Claude Code». У v3 **найімовірніший тримач лока — сам
   бекенд**, який працює постійно й тримає `.venv\Scripts\python.exe`.
   Порядок стає `stop → refresh → start`, а повідомлення про помилку має
   називати сервіс, а не сесії.
5. **`-Check` / `--check` розширюються:** сервіс піднятий чи ні, PID і порт,
   чи відповідає `/health`, чи є `api.token`, чи парситься `banks.json`,
   скільки банків зареєстровано.
6. **`state/` поповнюється** — `banks.json`, `api.token`, `service.db`,
   `service.json`. Обидва інсталятори вже ніколи не чіпають `state/`;
   лишається це **підтвердити тестом**, бо ціна помилки зросла: тепер там не
   лише відтворюваний індекс, а й реєстр банків, який руками не відновиш.
7. **`requirements.txt` уже оновлено** (fastapi, uvicorn, watchdog, httpx;
   `fastmcp` свідомо відсутній) — і `install.ps1 -Check` уже їх пробує.
   Це єдиний пункт списку, який на момент запису **зроблено**.

### 15.7 `embedder.py` × `cpu*3//4` — реальна взаємодія є **[NEW]**

Читання злитого коду, а не припущення. 43 рядки, які принесла гілка, — це
`_model_cache_spec()` + строгий `is_model_cached()`, тобто **валідація кешу**.
ONNX-налаштування (`allow_spinning=0`, `enable_cpu_mem_arena=False`) прийшли
раніше (`89e31c0`, `7860f78`) і гілка їх не чіпала. Пряма відповідь: **гілка й
зміна потоків не конфліктують**. Але одна взаємодія є, і вона неочевидна:

* Коментар у `src/embedder.py` стверджує: «Measured: peak 5407 MB →
  1563 MB, stable across batches». Це виміряно при **`cpu//3`** потоках.
  Фаза 0 підняла стелю до `cpu*3//4` (у `config.py` вже
  `EMBED_THREADS_FRACTION = (3, 4)`).

  **Поправка: гіпотеза «пік RSS масштабується з кількістю потоків» —
  ХИБНА, і це виміряно.** Тут стояло, що з вимкненою ареною кожен
  intra-op-потік алокує транзієнтно, тож пік росте з потоками.
  Контрольований A/B **9 проти 4 потоків дає різницю 1.0 МБ**. Пік
  залежить не від потоків, а від **розміру батчу й найдовшої послідовності
  в батчі**. Ніде не приписувати піковій памʼяті стелю потоків.

  Що виміряно натомість (Windows working set): **усталений стан
  1503–1526 МБ** на всіх пробуваних навантаженнях (розкид 22 МБ), а **пік** —
  ~1.56 ГБ на коротких англійських чанках і **~2.07 ГБ на українському
  markdown** при `BATCH_SIZE=16`. Історичні 5407 МБ — майже напевно Linux RSS
  і **не відтворені**. NFR-9 переписано під це: ~1.6 ГБ — це усталений стан,
  **не** пік; памʼять цього проєкту переважно українська, тож орієнтир —
  верхня межа.

  **Наслідок, який здається оптимізацією, але нею не є: сортувати чанки в
  батчі за довжиною не треба.** З «пік тримає найдовший чанк» напрошується
  «групуймо схожі за довжиною», але fastembed **уже враховує довжину**: батч
  переважно коротких чанків з одним 512-токенним коштує **539 МБ**, батч
  суцільних 512-токенних — **736 МБ**. При padding-у до максимуму батчу ці
  числа збіглися б. Основне вже відіграно; ідея закрита **заміром**, а не
  думкою, і не подається як можливість.

  *Лишається зробити:* engine-dev оновлює коментар у `embedder.py` фактичними
  числами — точна цифра, яка стала неправдою, гірша за відсутність цифри.
  Замір фази 0 по throughput дав **≈×1.5 (12 CPU, мала фікстура)**: напрям
  підтверджено, NFR-5 закрито, точнішої цифри не фіксуємо (обидва способи
  ганяли ту саму фікстуру крізь того самого резидента, тож збіг був
  арифметичний, а не незалежний).
* Спінінг вимкнено, тож зростання потоків **не** повертає проблему
  «холості ядра на 350%» — цей ризик закритий і перевіряти його не треба.
* Клемп `min(override, cpu)` і гілка `sched_getaffinity` збережені в
  `_embed_threads()` — перевірено, регресії немає.

**Другий ефект строгого `is_model_cached()`.** Він тепер вимагає **повного
снапшота** саме нашого репозиторію. `providers/local.py` (§2.1) падає в
in-process фолбек лише `if is_model_cached()`, тож там, де v2 з «майже
завантаженою» моделлю ще шкутильгав, v3 підніме `EmbeddingUnavailable`.
Це **правильніше**, але користувач має розуміти причину:
текст помилки мусить розрізняти «модель не завантажена» й «снапшот
неповний — доверши `mnemo warmup`». Інакше діагностика зводиться до
здогадок.

### 15.8 `CLAUDE_PROJECT_DIR`: два резолвери поряд **[NEW]**

Гілка додала `CLAUDE_PROJECT_DIR` у ланцюг `config.resolve()`
(явний > `MNEMO_ROOT` > `CLAUDE_PROJECT_DIR` > cwd) — саме тому, що
Claude Code віддає його хукам і MCP, а `cwd` дочірнього процесу довіряти не
можна. Рішення правильне й **лишається**, але тепер у системі два резолвери:
`config.resolve()` (корінь проєкту, для `init`/`scaffold`) і
`registry.resolve(ref)` (банк, §6.4).

Зіткнення точкове й важливе: **auto-inject хук мусить слати
`CLAUDE_PROJECT_DIR`, а не `cwd`.** Форма `path` у `registry.resolve`
бере **найглибший** банк, чий корінь є предком шляху. Якщо хук пошле `cwd`
підтеки, а користувач має вкладений банк — запит піде не в той банк, тихо й
правдоподібно. З `CLAUDE_PROJECT_DIR` цього не станеться.
*Розвʼязаний стан:* §11.1, `hook-inject` бере корінь у тому самому порядку,
що й `config.resolve()`, і кладе його в поле `bank` запиту `/api/search`.

### 15.9 Документація й `_MEMORY_RULE` **[NEW]**

Половина цих файлів — просто docs-keeper у фазі 4–6:
`README.md` (розділи про stdio-MCP, хуки, `--scope`), `CLAUDE.md`
(мапа архітектури, список команд), `docs/Setup-design.md` (він **прямо
фіксує** `command: ${HOME}/…/bin/mnemo`, `args:["mcp"]` як контракт — рядки
43–45, і це найточніша суперечність із §10.4), скіл `mnemo-adopt`
(`SKILL.md`, `references/mnemo.md`, `references/memory-migration.md`).

Один пункт із цього списку **не документація, а код**, і його легко
проґавити: **`_MEMORY_RULE` усередині `src/scaffold.py`**. Це текст, який
`mnemo init` записує в кожен прийнятий проєкт як
`.claude/rules/mnemo-memory.md` — тобто в чужі репозиторії, у git. Він вчить:
«`memory_search` (scope `project`, and your agent scope when relevant)» і
«agent-specific knowledge → `.claude/agent-memory/<role>/`». У v3 скоупів
немає (рішення #13), а `search` таких аргументів не приймає вже
сьогодні. *Власник:* service-dev (файл його), *фаза:* 4, разом із рештою
`scaffold.py`. Текст має розповідати про плаский банк і про те, що
розділення — це окремі банки.

### 15.10 Живий `voice-agent` — що зачепить користувача **[NEW]**

Перевірено на диску (`E:\work_projects\python\voice-agent`), бо це єдиний
справжній споживач, а не гіпотеза.

| Що там є | Що станеться | Чи покрито |
|---|---|---|
| `.claude/settings.json` у git: хуки `mnemo ingest`, `mnemo hook-postedit`, `mnemo hook-inject` | після оновлення рушія кличуть v3-двійник | **так** — шими §11.3 (`hook-postedit` → no-op 0, `ingest` → аліас) |
| **`.mcp.json` відсутній**; є `.mcp.json.template` з формою **L1** (`/bin/sh`) + `mcp-setup.sh`, який генерує з нього | `mnemo init --migrate` бачить шаблон, переписує **свій** запис L1 → http-плейсхолдери, дописує значення в `.mcp.env` і три `sed`-рядки в `mcp-setup.sh` | **так [ЗАКРИТО]** — рішення §14.17; перевірено на копії цього самого шаблону, оригінал не чіпали |
| 61 файл у `.claude/memory/` + 86 у `.claude/agent-memory/` по 5 ролях (`developer`, `maria`, `planner`, `reviewer`, `tester`) | плаский банк скасовує поділ project/agent | **ні** — потрібне рішення про розкладку банків |
| Індекс `state/ca027e0006b0d45d.db` | ключ банку рахується інакше (POSIX + lower, корінь = корінь банку) → файл осиротіє | так, за задумом («міграція = перебудова з `.md`»), але перший білд — 147 файлів |

**Один із цих чотирьох рядків уже закрито** (шаблон — §14.17, 10.4A). Один
досі потребує рішення користувача:

1. **Розкладка банків для `voice-agent`.** Один банк на весь `.claude/`
   (просто, але пʼять агентів бачать памʼять одне одного) чи шість банків
   (проєктний + по одному на роль, з шістьма MCP-підключеннями)? Рішення #13
   каже, що ізоляція можлива **тільки** через окремі банки, тож це вибір між
   «спростити» і «зберегти нинішню поведінку». Це **рішення користувача**, не
   команди, — але його треба поставити явно, бо мовчазний вибір «один банк»
   тихо змінить поведінку пʼятьох агентів.
2. ~~**`.mcp.json.template` + `mcp-setup.sh`.**~~ **Закрито:** `mnemo init`
   тепер шаблон-обізнаний (10.4A). Він **не переписує чужі записи** — рішення
   Q2 в силі; він додає/мігрує **виключно свій ключ `mnemo`** у шаблоні,
   дописує свої змінні в `.mcp.env`/`.mcp.env.example` і свої три `sed`-рядки
   в `mcp-setup.sh`. Це і є хазяйська поведінка в проєкті з такою конвенцією:
   писати прямо в `.mcp.json` там означало б, що запис зникне при наступному
   `bash mcp-setup.sh` — тихо. Форму фрагментів задає користувацький скіл
   `project-mcp-setup` (`templates/mnemo.example`), і вона відтворена дослівно.

Перевірка ключів індексу для протоколу (обидва живі проєкти):

```
E:\work_projects\python\voice-agent
  v2  ca027e0006b0d45d   (наявний файл у state/)
  v3  84bd01f0eac2fd02   (той самий корінь, posix+lower)
  v3  70d1c48d60078747   (якщо корінь банку = <проєкт>\.claude)
```

---

## 16. Шов фаз 2→3→4: де контракти фази 2 доведеться міняти

> Розділ **випереджальний**, а не описовий: `api.py` будується зараз, під
> контракти, які фази 3 (черга, watcher, WS) і 4 (обличчя) розширюватимуть.
> Нижче — місця, де форма з фази 2 **не витримає** появи справжнього воркера,
> і мінімальна правка для кожного. Усе в цьому розділі — **[NEW]**.
>
> Там, де знайдено помилку у **власному** контракті, вона названа помилкою:
> дешевше визнати її тут, ніж у фазі 3.

### 16.1 WebSocket: чого UI не зможе намалювати за §9.7

Коротка відповідь на питання «чи вистачає конверта, щоб показати глибину
черги по банку й прогрес задачі без полінгу»: **прогрес — так, глибину по
банку — ні.**

**G1. Подія `queue` — глобальна.** Її `data` — `{depth, high, normal, low,
current}`, тобто числа **по всьому сервісу**. А список банків (FR-7) показує
«скільки у черзі» **для кожного банку**. З глобальної події це не
відновлюється: UI не знає, які з 12 задач належать банку `notes`.
*Мінімальна правка:* додати в `queue.data` мапу
`"by_bank": {"<bank_id>": {"depth": 3, "indexing": true}}`. Воркер цю
розкладку вже має — це один словник у тій самій події, а не N подій.

**G2. Тригер `bank_status` не визначений.** §9.7 каже «стан банку змінився».
Через `BankInfo` там є і `queued`, і `indexing` — теоретично це другий шлях
до per-bank лічильника. Але без визначеного тригера на нього не можна
покластися: якщо подія йде лише після `index_done`, лічильник відстає на цілу
задачу.
*Мінімальна правка:* лишити `bank_status` для **повільних** змін
(перейменування, провайдер, `enabled`, `last_error`), а лічильники віддавати
**тільки** через `queue.by_bank` з G1. Одне джерело правди на лічильники
замість двох, які можуть розійтися.

**G3. Немає контракту повторного підключення.** WS рветься (сон ноутбука,
рестарт сервісу). Після реконекту UI має стан із минулого й **жодного способу
дізнатися, що він щось пропустив** — послідовності немає.
*Мінімальна правка, найдешевша з усіх тут:* зафіксувати, що **`hello` — це
сигнал «перечитай усе»**. Початковий стан UI бере з REST (`GET /api/banks` +
`GET /api/status`), WS — **виключно дельти, ніколи не джерело правди**. Тоді
реконект самолікується без жодної серверної механіки. Додатково — монотонний
`seq` у конверті (на зʼєднання), суто щоб розрив було видно в логах.
*Наслідок для §9.7:* `hello.banks` як список голих `id` лишається корисним
лише для звірки «сервер бачить ті самі банки»; будувати з нього список банків
не можна, і це треба сказати ui-dev прямо.

**G4. Життєвий цикл `BankInfo.last_error` не визначений.** Дизайн §7 вимагає
показувати помилки в списку банків, але хто пише `last_error` і **коли він
зникає** — ніде.
*Мінімальна правка:* `last_error` = остання подія `index_error` цього банку;
**очищається першим успішним `index_done`** цього ж банку. Інакше червоний
значок висить вічно після однієї давньої помилки.

### 16.2 Черга: «витісни bulk заради цього файлу» — як написано, **не виражається**

Пряма відповідь: **ні**, і дефект у §8.3 цього ж документа.

**D1 — головний. Фаза сканування не витісняється взагалі.** §8.2 каже, що
`bulk` робить `scan_bank` + `build_plan` і **сам не ембедить**. Але
`scan_bank` рахує sha256 **кожного** `.md` банку — секунди на сотнях файлів і
десятки секунд на тисячах, і весь цей час воркер зайнятий, а точок виходу
немає. Твердження §8.3 «найдовше, що блокує чергу, — один файл» для цієї фази
**хибне**: блокує повний обхід банку.

Це не теоретично: щойно зареєстрований банк — саме той момент, коли
користувач найімовірніше редагує файли й найгірше сприйме затримку.

*Мінімальна правка:* `bulk` **виконується поза воркером**. Сканування — чисте
читання + хешування: воно не звертається до провайдера, не пише в БД і не
потребує серіалізації з писакою. Тому йде окремим потоком і **стрімить**
`enqueue_file(...)` у міру знаходження змін; воркер лишається вільним для
`HIGH`. Потрібен лише **guard «скан цього банку вже в польоті»**, щоб два
`bulk` не подвоїли задачі (дедуп §8.3 підстрахує на рівні файлів, але це не
має бути основним механізмом).

**D2. Голодування `LOW` під тривалим потоком `HIGH`.** Симетрична проблема до
тієї, заради якої вводили пріоритети: активна сесія редагування генерує
`HIGH` безперервно, масовий білд не завершується ніколи. Контракт про це
мовчить.
*Мінімальна правка:* лічильник анти-голодування — після
`MNEMO_QUEUE_STARVATION_N` (типово **20**) поспіль виконаних `HIGH` воркер
бере одну `LOW` поза чергою. Це лічильник, не архітектура. Якщо тимлід
вважає це передчасним — тоді треба принаймні **записати**, що голодування
можливе, а не лишати його невидимим.

**D3. Промоція задачі, що вже має `start_batch`.** Дедуп §8.3 каже: новий
пріоритет вищий → наявна задача видаляється й кладеться заново. Але наявна
може бути **відновленою** (`start_batch=7`). Промоція мусить **зберегти
`start_batch`**, і це безпечно **тільки** завдяки перевірці sha256 перед
продовженням: якщо користувач змінив файл (а він змінив — тому й прилетів
`HIGH`), перевірка поверне задачу на `start_batch=0` разом із `delete_file`.
Ланцюжок правильний, але зараз виводиться з двох різних абзаців; його треба
назвати одним реченням, інакше його реалізують навпаки — скинуть
`start_batch` у промоції й втратять зроблені батчі там, де файл не змінювався.

**Три мовчанки, які варто зафіксувати (це не помилки, але їх дочитують
навпаки):**

* `HIGH` **не витісняє** `NORMAL`, і не повинен: `NORMAL` — це один файл із
  UI, обмежений тим самим бюджетом, що й `HIGH`. Зараз це видно лише з умови
  `current.priority == LOW` і читається як недогляд.
* «Затримка — один батч» варто перекласти в час: батч — це **один виклик
  `embed_passages` на ~16 пасажів**, тобто одиниці секунд на CPU (плюс ~3 с,
  якщо модель холодна). Порядок величини — секунди, не мілісекунди.
* **Напрям залежності:** `index.py` (D, engine-dev) отримує `should_yield`
  колбеком і **не імпортує** `workqueue`. §8.4 фіксує це для черги; зворотний
  бік не сказаний, а шов тут саме між власниками.

### 16.3 Статус у `api.py`: механічно так, але три пастки

Формула §5.2 потребує `workqueue.depth(bank_id)`, `workqueue.busy(bank_id)`
(§8.4) і `store.chunk_count(conn)` (§3.3). Усі три існують. Але:

**T1 — найгірша. `busy(bank_id)` мусить означати «поточна задача належить
ЦЬОМУ банку», а не «воркер узагалі зайнятий».** У другому прочитанні під час
масового білду банку A **кожен** банк відповідатиме `indexing`, і перевірка
фази 3 («під час масового білду банку A правка в банку B…») перевірятиме
нісенітницю, бо статус B буде неправдивий. Сигнатура
`busy(bank_id: str | None = None)` це припускає, але ніде не сказано — рівно
та помилка, яку легко зробити й важко помітити.

**T2. Порядок читання.** Спершу `queued`/`busy`, **потім** `chunk_count`.
У зворотному порядку є вікно: прочитали `chunk_count = 0`, воркер дописав
чанки й спорожнив чергу, читаємо `queued = 0` → віддаємо `empty` для банку,
який щойно проіндексувався. У правильному порядку найгірше — зайвий
`indexing`, який самовиправиться наступним запитом.

**T3. Вікно, яке створює правка D1.** Якщо `bulk`-скан іде **поза** воркером,
то під час нього `depth == 0` і `busy == False` → статус скаже `empty`/`ready`,
поки скан знаходить 500 змінених файлів. Це брехня рівно в той момент, коли
користувач щойно додав банк і дивиться на екран.
*Правка:* `busy(bank_id)` повертає `True` також, якщо для банку **є скан у
польоті**. D1 і T3 — одна зміна, і робити їх треба разом.

**T4 — не коректність, а навантаження, але проєктувати треба зараз.**
`store.connect()` на кожному виклику виконує `_ensure_schema` —
`executescript` з `CREATE TABLE IF NOT EXISTS` і `CREATE VIRTUAL TABLE`.
Якщо `api.py` відкриватиме зʼєднання **на кожен запит**, це DDL на кожен
пошук, у конкуренції з єдиним писакою (`busy_timeout=5000` витримає, але
платити за це на кожному запиті безглуздо).
*Правка:* `api.py` тримає **одне довгоживуче read-зʼєднання на банк**
(ліниво створене, кешоване), інвалідоване при `rebuild` і при
`DELETE /api/banks`. Писака — окреме зʼєднання воркера. WAL це дозволяє й
саме для цього обраний.

### 16.4 MCP: що де-ризикувати **зараз**, у фазі 2

Ризик §10.3 (чи видно тулу HTTP-заголовки) — не єдиний і навіть не
найбільший. Чотири дії, усі дешеві, усі у фазі 2:

**N1. Один резолвер на всі обличчя.** Виділити в `api.py` єдину функцію
`resolve_bank_ref(explicit: str | None, header: str | None) -> Bank` і
викликати її **і** з REST-хендлерів, **і** з MCP-тулів. Тоді роль MCP-шару
зводиться до «дістати заголовок», а якщо заголовок виявиться недоступним —
запасний шлях (аргумент `bank`, єдиний банк) **уже написаний і
протестований**, а не дописується в паніці у фазі 4.

**N2. Змонтований MCP кличе внутрішні функції, а не сам себе по HTTP.**
Формула «обличчя — тонкі клієнти API» правильна для CLI, хука й UI, які
живуть в **інших** процесах. MCP-фасад живе **всередині** того самого
uvicorn: self-HTTP дав би зайвий сокет, друге логування події й можливість
дедлоку на воркерах. Пінимо явно, бо буквальне читання §6 дизайну
підштовхує саме до self-HTTP.

**N3. Аутентифікація мусить накривати `/mcp` до MCP-хендшейку.** Токен
перевіряє ASGI-middleware **перед** застосунком MCP і повертає звичайний
401. Інакше 401 прилетить усередині `initialize` і Claude Code покаже щось
нечитабельне. Перевірити на живому підключенні рано, а не в кінці фази 4.

**N4. Два спайки по ~30 хвилин, і перший важливіший за питання про
заголовки.**

| Спайк | Питання | Що ламається, якщо «ні» |
|---|---|---|
| **S1** | Чи Claude Code взагалі надсилає `headers` з `.mcp.json` для `type: http`? | Якщо ні — **не доїде й `Authorization`**, тобто валиться не адресація банку, а **вся модель автентифікації MCP** (§9.1, §10.4). Більший ризик, ніж S2, і перевіряється швидше. |
| **S2** | Чи бачить тіло тула ці заголовки через `mcp` SDK? | Якщо ні — лишаються аргумент `bank` і фолбек «єдиний банк»; UX гірший, контракт живий. |

Порядок саме такий: **S1 перед S2**. Якщо S1 негативний, S2 не має сенсу, а
§9.1/§10.4 треба переглядати цілком (варіанти: токен у шляху URL або
loopback-довіра без токена для `/mcp` — обидва вимагають рішення тимліда, бо
чіпають NFR-7).

*Запасний варіант адресації, якщо S2 негативний:* монтування по шляху —
`/mcp/{bank_name}`. Засторога: монтувати N під-застосунків «по банку» на
живому сервері незручно (банки додаються в рантаймі), тож це має бути **один**
маунт із параметром шляху, який MCP-застосунок бачить. Теж предмет спайку, а
не припущення.

### 16.5 Що з цього змінює саме фазу 2

| Зміна | Де | Чому не можна відкласти |
|---|---|---|
| `queue.data.by_bank` | §9.7 | інакше UI фази 6 не має джерела для per-bank лічильника, а форму події вже зафіксують клієнти |
| `hello` = «перечитай усе»; WS — тільки дельти | §9.7 | правило поведінки, не код; вписати до того, як ui-dev почне бутстрапитись із WS |
| `bank_status` — лише повільні зміни | §9.7 | розводить два джерела лічильників, поки їх ще ніхто не читає |
| `last_error` ставиться / очищається | §9.5 | поле вже є в `BankInfo`, семантики немає |
| `busy(bank_id)` = «цей банк» + скан у польоті | §8.4, §5.2 | від цього залежить формула статусу, яку `api.py` пише **зараз** |
| порядок читання статусу | §5.2 | один рядок, інакше рідкісний хибний `empty` |
| довгоживуче read-зʼєднання на банк | §9.1 | архітектура доступу до БД в `api.py`; переробляти потім дорого |
| `resolve_bank_ref` як єдина точка | §10.3 | пишеться у фазі 2 разом із REST; у фазі 4 вже пізно |
| спайки S1 / S2 | §10.3 | результат S1 може змінити §9.1 і §10.4 |

Решта (позачерговий скан `bulk`, анти-голодування, промоція зі
`start_batch`) — це **фаза 3**, але виконавець фази 2 має знати про них уже
зараз, бо `busy()` / `depth()` він викликає з першого дня.
