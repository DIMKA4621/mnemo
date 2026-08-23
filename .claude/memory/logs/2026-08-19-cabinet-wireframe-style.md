# 2026-08-19 — wireframe повернувся до візуальної мови живого кабінету

Перший інтерактивний wireframe підтвердив інформаційну архітектуру: лівий rail, три сторінки, окремий master-detail Journal, точні snapshots і переходи у Memory сподобались.
Візуальний skin відхилено як надто Android/CRM: зовнішній IBM Plex, великі provider cards, rounded result cards та icon tiles виглядали як стандартний dashboard kit, а не mnemo.

## Узгоджена корекція

Структуру й взаємодії не змінено; wireframe перешкірено під чинний продукт:

- full-width topbar із `mnemo`, service bits, темою й live-станом;
- compact flat rail починається під topbar;
- нативні `Segoe UI` + `Cascadia Mono`, без Google Fonts;
- точні чинні theme tokens, щільні controls і one-pixel pane separators;
- Memory майже буквально продовжує наявний трипанельний екран;
- Journal використовує щільні event rows і плоскі bordered results;
- Settings повернув segmented `Локальний резидент / Ollama / OpenAI` замість трьох великих карток.

Перевірено в Chrome на desktop і 360 px, темній темі; horizontal overflow і console errors відсутні.
Lighthouse після перешкірювання: accessibility 100, best practices 100. Код продукту не змінювався — оновлено лише прототип у `.claude/scratch/`.
