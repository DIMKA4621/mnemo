# 2026-08-17 — паритет кабінету: pending перебудова, один doctor і явні сироти

Коміт: `feat(ui): close the cabinet's CLI parity gap`.

Закриває D у погодженому порядку A→B→C→D: головний екран тепер показує `REBUILD PENDING`; «Обслуговування» має `doctor` і `clean-orphans`; чотири дрібні вади наскрізної перевірки закриті разом.
**`warmup` свідомо не ввійшов** — користувач обрав винести його окремо.

## Один doctor, а не текст як API і не дві реалізації

Користувач сформулював правильну межу: команда має одержувати **дані**, CLI показує їх текстом, UI — візуально.
Звідси `src/diagnostics.py`:

- `collect()` повертає JSON-shaped report без секретів;
- `render_text()` — представлення для `mnemo doctor`;
- `GET /api/doctor` віддає ті самі факти кабінету.

API не захоплює stdout CLI й не парсить прозу.
Сервіс також не стукає HTTP у самого себе: CLI робить loopback health probe, а API підставляє PID/чергу, які вже знає.
Endpoint у doctor **лише описується** — жодного paid embedding.

Це заразом закрило ваду isolated `-InstallHome`: resident/backend/token rows несуть `scope/source`, а текст буквально каже `[machine port]`.
Тимчасовий home із нулем банків більше не видає справжній backend із двома за власний.

## Cleanup приймає показані id, не «all»

`doctor` лишився read-only.
Окрема дія `POST /api/clean-orphans` приймає список id, який кабінет щойно показав.
`diagnostics.delete_orphans()`:

1. перечитує registry й будує **свіжий** orphan-list;
2. відкидає id, якого в ньому вже немає;
3. для кожного решти кличе `registry.delete_index()`, а той перечитує registry ще раз просто перед unlink.

Тобто банк, який зʼявився між показом і підтвердженням, стає `skipped`, не втратою індексу.
Нечитаний `banks.json` → `orphan_cleanup_refused` (409) і нуль видалень.
Відповідь розділяє `removed / skipped / locked`; `freed_bytes` рахує лише повністю видалене.
CLI після `[y/N]` використовує ту саму функцію й далі працює при мертвій службі.

## `REBUILD PENDING` — не червона помилка

Застарілий індекс — тривалий стан машини, не невдалий HTTP-запит.
Тому він має окремий warning lane, а не старий `#banner`, який будь-який успішний клік ховає.

Три групи:

- searchable + idle → кнопка «Перегенерувати»;
- already indexing → лише звіт, без дубля задачі;
- disabled → названий окремо, спершу треба ввімкнути.

Підтвердження показує точні банки й чанки, обіцяє недоторкані `.md` і каже **≈3× end-to-end**, не 8.8× embedding-only.
Другого mass endpoint немає: кабінет паралельно викликає чинний `/api/reindex full:true` для показаних банків і зберігає часткові відмови.

## Рестарт був не потрібен — і напис вів не туди

`PUT /api/settings` вже кликав `forget_providers()`, тож `restart_required: true` суперечив коду й живому виміру.
Тепер `false`: поточний file-task закінчує зі своїм handle; наступний бачить новий `provider_key`, не домішує вектор до старого простору й ескалює в rebuild.
Після Save UI перечитує embed-state + banks + status: новий backend чинний, а справжня наступна дія (`REBUILD PENDING`) видна одразу.

## Hosted endpoint теж можна перевірити

`POST /api/embed/load` уже був probe, але кабінет під `holding: n/a` не показував жодної кнопки.
Тепер є **«Перевірити ендпоінт»** із попередженням: один embedding request може тарифікуватися.
Unload не зʼявився.
Успіх лишає `holding: n/a` і додає `probe_dim`; не бреше «модель у памʼяті» про hosted API.

У devserver знайшлась похідна вада: fixture відмовляла `n/a` **для обох** дій, хоча real API відмовляє лише unload.
Саме тому devserver мусить рости кожним маршрутом — інакше макет перевіряє інший продукт.

## `git check-ignore -v`: непорожній stdout ще не означає ignored

`init` після seed питає git, чи поїдуть `MEMORY.md` і правило.
Broad `**/.claude` тепер дає гучний NOTE з match, наслідком і вузькими винятками; mnemo не переписує людську ignore-політику.

Перший тест зловив пастку в самому probe: після виправлення verbose-вивід показує рядок `!.claude/memory/**` — **negation, яка врятувала шлях**. stdout усе ще непорожній.
Треба парсити pattern і відкидати `!`, а не читати «є рядок» як «ignored».
Так само `git check-ignore -q` не приймає кілька pathname разом у використаній формі — перевіряються окремо.

## Перевірено

- `tests/test_platform.py`: **303 passed**, 0 failed;
- `tests/test_pipeline.py`: **81 passed**, 0 failed;
- live `tests/test_mcp.py`: **44 passed**, 0 failed (черга була порожня, відома mirror-race не спрацювала й не маскувалась);
- isolated real FastAPI на 8929: `/api/doctor` має 11 секцій і не містить токена; settings PUT → `restart_required:false`; cleanup видалив тільки `deadbeef…`, `service` і `../outside` стали `skipped`; `/api/*` не зʼявився в OpenAPI;
- end-to-end `init` у тимчасовому git repo з `**/.claude`: warning є, broad rule не переписаний, wiring повний; - dev cabinet: pending idle/running/disabled, mass rebuild, structured doctor, два orphan shapes, partial cleanup (1 removed + 1 locked), hosted `n/a` probe й mismatch `probe_dim`; light/dark; - viewport **360/420/520/700/1000 px**: `document.scrollWidth == innerWidth`, settings-body всередині frame. Для 360 rail стає top-row, stat rows складаються; заразом довелось сховати topbar service bits і переносити journal filters — інакше сама шестерня була за правим краєм; - shared engine оновлено штатним `install.ps1 -NoModel`: stop → refresh → start, serving PID **20196**. Живий cabinet показав structured report і наявну **1 сироту / 4.2 MiB**; її **не видаляли**.
  Повторний PUT того самого `local` повернув `restart_required:false`, PID лишився 20196, pending банків нема.

## Що лишилось

- `warmup` button: окремий контракт із фоновим процесом/progress/reconnect; виклик у FastAPI завантажив би 1.5 ГБ session у неправильний процес.
- Наступний погоджений UI-крок — checkbox «одразу підключити проєкт (MCP)» у діалозі додавання банку (`topics/deferred.md`).
- Відома гонка byte-identical mirror у `test_mcp` не чіпалась.
