# AI Fitness Coach: Adaptive Training from Your Own Session History

A command-line training assistant that learns from every workout you log. Claude designs your next session from your profile and recent history, and a deterministic safety engine verifies it before you ever see it.

---

## Project Overview

Many people follow generic workout plans that ignore how their last session actually went. They push through pain, repeat hard days back to back, or never progress because nothing adapts to their effort. Static plans cannot react to a high perceived exertion or a sore knee, and a purely AI-generated plan carries its own risk: a language model can confidently suggest a session that is unsafe for the person in front of it.

**AI Fitness Coach** solves this by combining the two. It accepts your profile, goal and health concerns, then records the pace, heart rate, effort and pain from each session. Claude uses that history to plan the next session, and a rule-based logic layer checks every suggestion before it reaches you.

> **The AI proposes. The code decides.**

---

## Features

* **History-Driven Adaptation:** Each request to Claude carries your profile and your last 5 sessions (planned versus actual), so plans progress when sessions feel easy and ease off after high effort or pain.
* **Dynamic AI Generation:** Produces a structured session (exercise type, duration, intensity, target heart rate and pace, warm-up, main set, cool-down and a rationale) tailored to your goal and fitness level.
* **Deterministic Safety Verifier:** A rule engine rejects unsafe suggestions regardless of what the AI says, for example high intensity for a beginner, right after a painful session, or when health concerns are listed. Rejected drafts go back to Claude with the reasons, up to 3 times.
* **Warnings and Scoring:** Acceptable but borderline sessions are flagged with a visible warning and a score, such as a sudden jump in duration.
* **Accept or Reject With a Reason:** Reject a suggestion and tell the coach why; the next attempt adapts to your feedback.
* **Input Validation:** Every prompt re-asks until the value is the right type and in range, and logged sessions with unrealistic pace or heart rate are refused.
* **Failure-Tolerant by Design:** API failures, timeouts, malformed AI output, and missing or corrupt data files are all handled without crashing, and a corrupt data file is backed up instead of overwritten.

---

## How It Works

1. **Setup:** Enter your age, height, weight, fitness level, prior health concerns and goal.
2. **Suggestion:** Claude receives your profile and recent history and returns the next session as JSON.
3. **Safety check:** The logic layer accepts, flags or rejects it.
4. **Your decision:** Accept it, or reject it with a reason.
5. **Log it:** After training, record duration, pace, average heart rate, RPE, pain and comments. The next suggestion is built from this result.

```
User -> io_manager -> ai_manager (Claude) -> logic_manager -> data_manager (JSON file)
                          ^                        |
                          +---- rejection reasons -+   (retry loop, run by main.py)
```

---

## Project Structure

| File | Role |
| --- | --- |
| `main.py` | Controller. Wires the four managers together; no printing, no business rules |
| `io_manager.py` | Input layer. The only file with `print()` or `input()`; validates and re-prompts |
| `ai_manager.py` | AI layer. The only file that calls Claude; checks the JSON shape of the answer |
| `logic_manager.py` | Logic layer. Pure functions that enforce the safety and business rules |
| `data_manager.py` | Data layer. Reads and writes one JSON file |
| `test_logic_manager.py` | Offline tests with hardcoded sample AI responses |
| `Dockerfile`, `requirements.txt` | Container and dependency definition |

The code is 100% procedural: there are no class definitions anywhere.

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

* Single user on one machine, with no accounts.
* Maximum heart rate is estimated as 220 minus age, a population average rather than a measured value.
* Rules are fixed thresholds and are not personalised.
* This is not medical advice. Anyone with a medical condition should consult a professional.
