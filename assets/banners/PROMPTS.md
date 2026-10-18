# Генерация заставок

Способ: встроенный инструмент image_gen, отдельный вызов для каждой заставки.
Референс: присланный пользователем логотип «Доктор на связи».
После исходной генерации все десять изображений, включая `client_main.png`,
отдельно отредактированы для замены фона на белый. Изображения проверены
визуально перед подключением.
Размер изображений разделов: 1672 × 941; главной заставки: 1448 × 1086, PNG.

Использованный шаблон промпта (в каждом вызове TITLE заменён соответствующим заголовком):

```text
Use case: ads-marketing. Create ONE finished wide landscape Telegram bot section banner, aspect 16:9. Brand: Доктор на связи. Image 1 is a STYLE AND LOGO REFERENCE, not the composition to reproduce. Clean minimalist black opaque background. Very large centered exact Russian heading "TITLE" in bold rounded sans-serif, blue-to-turquoise gradient matching the reference logo, subtly dimensional smooth overlapping gradients, crisp professional edges. Heading must fit completely with generous 12% outer margins, centered around the vertical middle. For long heading use two balanced lines if necessary. Small recognizable blue/turquoise ДС medical-plus brand monogram from reference in bottom right, without tiny lettering. No other words, no slogan, no mockup, no border, no photographic people, no extra objects, no watermark. Flat straight-on finished graphic, consistent restrained layout across the banner family. Preserve Cyrillic spelling exactly.
```

Шаблон второго этапа (редактирование каждого изображения с предыдущей версией
в качестве входного файла):

```text
Replace only the background with solid opaque pure white (#FFFFFF). Keep the original Russian heading, blue-to-turquoise colors, brand monogram, proportions, composition, and clean edges unchanged. Do not add or remove any foreground elements. Preserve Cyrillic spelling exactly.
```

| Файл | TITLE |
| --- | --- |
| experts.png | ЭКСПЕРТЫ |
| tariffs.png | ТАРИФЫ |
| subscriptions.png | ПОДПИСКИ |
| consultations.png | КОНСУЛЬТАЦИИ |
| referral.png | РЕФЕРАЛЫ |
| help.png | ПОМОЩЬ |
| expert_main.png | КАБИНЕТ ЭКСПЕРТА |
| admin_main.png | УПРАВЛЕНИЕ |
| finance.png | ФИНАНСЫ |
