# Как мы работаем

## Роли
- **Постановщики задач и проверяющие** — все участники.
  Пишут задачи на GitHub, проверяют результат.
- **Оператор агента** — Арсений.
  Запускает ИИ-агента (Claude Code), открывает Pull Request, вливает его в main.

## Что нужно участнику (один раз)
1. Аккаунт на GitHub и принятое приглашение в репозиторий.
2. Установить:
   - Git — https://git-scm.com/downloads/win
   - Python 3.12+ — https://www.python.org/downloads/
     (при установке отметить «Add python.exe to PATH»)
   - VS Code — https://code.visualstudio.com/ (для чтения и правки файлов)
3. В PowerShell:
```
   git config --global user.name "Ваше имя"
   git config --global user.email "почта_как_на_github@example.com"
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   cd ~
   git clone https://github.com/Guapchich1/car-owner-assistant.git
   cd car-owner-assistant
   python -m venv .venv
```
   При первом обращении к GitHub откроется окно входа — войти через браузер.

Claude Code устанавливать не нужно — его запускает только оператор агента.

## Как проходит задача
1. **Участник пишет задачу:** вкладка Issues → New issue → «Задача для агента».
   Главное — критерии приёмки: по ним потом проверяем результат.
2. **Арсений читает задачу.** Если что-то неясно — спрашивает в комментариях к Issue.
3. **Агент выполняет задачу** в отдельной ветке.
4. **Арсений открывает Pull Request** и назначает проверяющим автора задачи.
5. **Автор задачи проверяет PR** у себя на компьютере (см. ниже) и на GitHub:
   вкладка Files changed → Review changes →
   - Approve — если всё по критериям;
   - Request changes — если нет, с комментарием, что исправить.
6. **Арсений вливает PR в main** и делает запись в docs/agent-log.md.

## Как проверить PR у себя
1. Узнать имя ветки — оно написано вверху страницы PR (например, `feature/vin`).
2. В PowerShell:
```
   cd ~\car-owner-assistant
   .venv\Scripts\Activate.ps1
   git fetch
   git checkout feature/vin
   python -m pip install -r requirements.txt
   python -m pytest
```
3. Запустить приложение и проверить по критериям приёмки из задачи
   (команда запуска будет в CLAUDE.md, когда появится код).
4. Вернуться на main: `git checkout main`.

## Правила
- В main попадает только через Pull Request, одобренный не автором.
- Одна задача — одна ветка — один PR.
- Задачи пишем по шаблону, с конкретными критериями приёмки.