# AegisFit: Adaptive Training from Your Own Session History

A training app that learns from every workout you log. Claude designs your next session from your profile and recent history, and a deterministic safety engine checks it before you see it. It runs as a Flet GUI (desktop window or browser, including Docker), and the original terminal menu is still available.

> **The AI proposes. The code decides.**

---

## Project Overview

Many people follow generic workout plans that ignore how their last session actually went. They push through pain, repeat hard days back to back, or never progress because nothing adapts to their effort. A purely AI-generated plan carries its own risk: a language model can confidently suggest a session that is unsafe for the person in front of it.

**AegisFit** combines the two (the name comes from the *aegis*, the shield of Greek myth: the safety rules are a shield around every AI suggestion). It stores your profile, goal and health concerns, then records the pace, heart rate, effort and pain from each session. Claude uses that history to plan the next session, and a rule-based logic layer checks every suggestion before it reaches you.

---

## The GUI

| Screen | What you can do |
| --- | --- |
| **Splash** | Animated start screen while your saved data loads in the background |
| **Home** | This week's progress ring and intensity plan, "Plan my next session", the AI suggestion card (safety score, warnings, warm-up / main set / cool-down), **Accept** (pick the day in a date picker) or **Not for me** (quick-reason chips + free text sent back to the AI), the up-next session with Log / Reschedule / Discard, recent activity |
| **Calendar** | Month grid with markers for completed (teal), pain-reported (amber) and planned (blue) sessions; month navigation; *Pain only* and *Hard sessions (RPE 8+)* filters; month stats; tap a day to see its sessions and open the details |
| **Progress** | Weekly progress report (the Phase 2 feature): sessions, minutes, effort, pain, this week vs last week, a six-week training-load chart, observations, and an on-demand AI coach's note |
| **Profile** | Edit name, age, sex, height, weight (sliders and toggles), fitness level, days per week, minutes per session, goal, health concerns, equipment (checkboxes) and activities (chips). Live BMI and estimated max heart rate, equipment warnings, undo, and a "Reset all data" danger zone that requires typing RESET |
| **Settings** | Claude API key (masked, with **Test connection**) and model (Haiku / Sonnet / Opus); colour theme from presets or your own RGB hex codes, with a live preview and contrast check; week start day for the calendar; splash screen and animation switches; app and file locations |

The log-session dialog uses sliders for duration, heart rate, effort (RPE) and pace, a pain checkbox and a comments field.

AI calls run on a worker thread (`asyncio.to_thread`), so the window stays responsive while Claude is planning. If a draft breaks a safety rule, the progress text updates live while the coach revises it.

---

## Running the app

### 1. Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Add your Claude API key on the **Settings** tab after the app starts. Alternatively, set it in the shell before starting: `$env:ANTHROPIC_API_KEY = "sk-ant-..."` (macOS/Linux: `export ANTHROPIC_API_KEY=sk-ant-...`). A key saved in Settings takes priority.

### 2. Run

| Command | Result |
| --- | --- |
| `python main.py` | Desktop window (Flet installs its `flet-desktop` runtime automatically on first run) |
| `python main.py --web` | Serves the GUI at http://localhost:8550 |
| `python main.py --cli` | The original terminal menu |

### 3. Docker

The image serves the GUI as a web app on port 8550. No X server is needed.

Run these from the project folder:

```powershell
docker build -t aegisfit .
docker rm -f aegisfit                      # remove the previous container, if any
docker run -d --name aegisfit -p 127.0.0.1:8550:8550 -v "${PWD}/data:/app/data" aegisfit
docker logs -f aegisfit                    # optional: follow the log (Ctrl+C stops following, not the app)
```

Open **http://localhost:8550** and add your API key under **Settings**. `-d` runs the container in the background, so the terminal stays free; `docker rm -f aegisfit` stops it. The `-v` mount keeps your profile, history and settings in `./data` between runs. `127.0.0.1:` makes the app reachable from this computer only, not from other devices on your network.

### 4. Tests

```powershell
python -m unittest -v
```

All tests are offline: a stub `LLMClient` (or a fake `anthropic` module) stands in for Claude and `InMemoryStorage` stands in for the JSON file.

---

## Settings and colours

Settings are saved in `data/settings.json`, separate from your training data, so **Reset all data** keeps your key and theme. A damaged settings file is backed up and replaced with defaults.

**API key.** The key is stored on this computer only and is never sent back to the browser: the Settings tab shows only its first and last characters. Common paste mistakes (spaces, quotes, a whole `ANTHROPIC_API_KEY=...` line) are cleaned up, and a key that doesn't start with `sk-ant-` is refused. **Test connection** checks the key and model with Anthropic without planning a session.

**Colours.** The theme is four RGB hex codes: **accent** (buttons, progress, highlights), **background**, **cards** and **text**. The default is dark mode with a lime-green accent (`#A3E635`). You can change them two ways:

* in the app: **Settings → Appearance** (presets, or type your own hex codes and press *Apply colours*); or
* in code: the defaults in `ThemeColors` in `settings_manager.py`.

The other shades (navigation bar, borders, muted text, tinted badges) are blended from those four in `Palette.apply()` in `gui/theme.py`, and text on the accent turns black or white automatically. A theme is refused if text would fall below a 4.5:1 contrast ratio or the accent below 3:1 (WCAG 2.1), so the app can't be made unreadable. The status colours (completed, pain, planned, error) keep their meaning in every theme.

---

## How It Works

1. **Profile:** enter your details on the Profile tab.
2. **Suggestion:** Claude receives your profile, this week's plan and your last 5 sessions and returns the next session as JSON.
3. **Safety check:** the logic layer accepts, flags or rejects it. Rejected drafts go back to Claude with the reasons, up to 3 times.
4. **Your decision:** accept it and pick a day, or reject it with a reason.
5. **Log it:** after training, record duration, pace, heart rate, RPE, pain and comments. The next suggestion is built from this result.

```
Flet GUI (gui/)  or  console (main.py + io_manager.py)
                 |
           CoachService (coach_service.py)
      /          |             |            \
ai_manager   logic_manager   data_manager   (JSON file)
 (Claude)    (safety rules)  (storage)
```

---

## Project Structure

| File | Role |
| --- | --- |
| `main.py` | Entry point (`--web`, `--cli`) and `ConsoleApp`, the terminal controller |
| `coach_service.py` | Application layer used by both UIs: suggestion/safety retry loop, scheduling, logging, calendar events, weekly report, settings |
| `settings_manager.py` | `AppSettings`, `ThemeColors` and presets, colour/contrast maths, API key checks, the settings file |
| `gui/app.py` | `CoachGUI`: splash screen, bottom navigation, snackbars, date picker |
| `gui/base_view.py` | `AppView` abstract base class for the tabs |
| `gui/home_view.py`, `calendar_view.py`, `progress_view.py`, `profile_view.py`, `settings_view.py` | The five tabs |
| `gui/dialogs.py` | Log session, reject, confirm, reset and session-detail dialogs |
| `gui/widgets.py`, `gui/theme.py` | Shared components, colours and the Montserrat heading font (`gui/assets/fonts`) |
| `io_manager.py` | Terminal input/output (the only `print()`/`input()`), plus `ReportFormatter` wording shared with the GUI |
| `ai_manager.py` | The only file that calls Claude; builds prompts and validates the JSON reply |
| `logic_manager.py` | Safety rules, weekly planning and the weekly progress report |
| `data_manager.py` | JSON persistence, history queries and filters |
| `test_*.py` | Offline unit tests |
| `Dockerfile`, `requirements.txt` | Container and dependency definition |

### OOP highlights

* **Polymorphism:** `CalendarEvent` has two subclasses, `CompletedSession` and `PlannedSession`, and the calendar renders both without type checks. The shell drives every `AppView` subclass through `refresh()`/`root`. There are also the existing `FitnessRule`, `ProgressInsight`, `Storage`, `LLMClient`, `PromptBuilder` and `SessionFilter` hierarchies.
* **Inheritance and default behaviour:** `PrivateJsonFileStorage` extends `JsonFileStorage` to make the settings file owner-only. `LLMClient` gives every client no-op `configure()`/`verify()` methods that `ClaudeClient` overrides, so test stubs keep working unchanged.
* **Value objects:** `AppSettings` and `ThemeColors` are frozen dataclasses. Screens change settings by building a new object and passing it to `CoachService.save_settings()`, which validates it before anything is saved.
* **Composition / association:** `CoachGUI` creates and owns its views. Views keep a reference to the shell. `CoachService` is handed the managers it uses.
* **SRP / OCP / LSP:** screens only draw, `CoachService` only runs workflows, and managers keep their single responsibilities. A new tab, rule or insight is a new subclass, with no edits to the code that uses it.

---

## Safety Rules

| Outcome | Condition |
| --- | --- |
| **Reject** | Duration outside 10 to 120 minutes |
| **Reject** | Target heart rate above 90% of max (220 minus age) |
| **Reject** | Target pace outside 3.0 to 15.0 min/km |
| **Reject** | The AI rated its own session high risk |
| **Reject** | High intensity when health concerns are listed |
| **Reject** | Beginner given high intensity or a session over 60 minutes |
| **Reject** | High intensity after a session with RPE 8 or higher, or pain |
| **Reject** | Two high-intensity sessions in a row |
| **Flag** | Target heart rate below 40% of max |
| **Flag** | Duration more than 30% longer than the last session |
| **Flag** | The AI rated the session medium risk |

Each flag lowers the session score by 20 points, starting from 100.

---

## Limitations

* Single user, with no accounts (every browser tab of the web version shares one data file and one settings file).
* The API key is stored as plain text in `data/settings.json` (owner-only permissions on Linux and macOS). `data/` is in `.gitignore` and `.dockerignore`, so the key is never committed or built into the image.
* Maximum heart rate is estimated as 220 minus age, a population average rather than a measured value.
* Rules are fixed thresholds and are not personalised.
* This is not medical advice. Anyone with a medical condition should consult a professional.
